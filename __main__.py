"""A Python Pulumi program"""

import pulumi
import pulumi_akamai as akamai
import lookup_zones

# default TTL and limit of the string length we can add to certain records via the API
# API is failing if record lenght is too long. TXT records for example have a max of 255 chars.
TTL = 3600
LIMIT = 255

# quick hack to solve issues where we have to put multiple records into one
# in a next version we should clean it up and make some nice zone/record objects etc.
class DnsRecord:
    def __init__(self, resource_name, zone, record):
        """ this will initialize a DnsRecord object but won't create the record. """

        self.resource_name = resource_name
        self.name = record["name"]
        self.type = record["type"]
        self.targets = []

        # ttl field and should always be set, if not use 3600
        self.ttl = int(record.get("ttl", TTL))

        # zone is a pulumi Output object
        self.zone = zone

        # not all records have a prio set, so set to None if key not available
        self.prio = record.get("prio", None)

        self.append_target(record["value"])

    def append_target(self, value):
        # we can extend this method to so some more checks like look for double entries etc.
        self.targets.append(value)

    def create_record(self):
        """this will method will use info from object to add it to edgedns """
        # let's create the DNS records, every record has it's own setup of requirements
        # https://www.pulumi.com/docs/reference/pkg/akamai/dnsrecord/
        # we should get an array with the following fields
        # zone;name;record;target;priority;ttl
        # we use pulumi output object as we need to wait for the promise from the DnsZone call

        # making use of the pulumi Output object zone.zone for the zone so we create a depency between the zone resource and dns record
        # we need to wait until resource has been created.
        if self.type not in ["SRV", "MX"]:
            return akamai.DnsRecord(
                self.resource_name,
                recordtype=self.type,
                ttl=self.ttl,
                zone=self.zone.zone,
                name=self.name,
                targets=self.targets,
            )

        if self.type == "SRV":
            # this is our special SRV record, we need to add priority, weight, port, target
            # 100 1 5061 sipfed.online.lync.com.
            # in the .csv it's configured like this
            # 1 443 sipdir.online.lync.com;100
            # only taking first part into account.
            srv_record = self.targets[0].split()

            return akamai.DnsRecord(
                self.resource_name,
                recordtype=self.type,
                ttl=self.ttl,
                zone=self.zone.zone,
                name=self.name,
                priority=self.prio,
                weight=int(srv_record[0]),
                port=int(srv_record[1]),
                targets=srv_record[2].split(),
            )

        # MX records need separate priority field
        if self.type == "MX":
            return akamai.DnsRecord(
                self.resource_name,
                recordtype=self.type,
                ttl=self.ttl,
                zone=self.zone.zone,
                name=self.name,
                targets=self.targets,
                priority=self.prio,
            )


def create_zone(zone, contract_id, group_id):
    """ create an EdgeDNS zone, we need zone name, contract_id and group_id"""
    # when we have a contract_id and group_id, let's create a new EdgeDNS resource
    # https://www.pulumi.com/docs/reference/pkg/akamai/dnszone/
    # if you see Create_Handler errors that's probably because zone is still in EdgedDNS but not as pulumi resource!

    return akamai.DnsZone(
        zone,
        comment="managed by pulumi",
        contract=contract_id,
        group=group_id,
        sign_and_serve=False,
        type="primary",
        zone=zone,
    )


# A pulumi project can have different stacks which their own state.
# select array of zones from stack, these zones will be copied to EdgeDNS
# $ pulumi config set --path zones[0] grinwis.com
# $ pulumi config set --path zones[1] grinwis_test.com
# $ pulumi config set zones '["grinwis.com", "shadow-it.nl"]'
# or use a filename with zones, filename being the preferred optin
config = pulumi.Config()

# check if the key is there, it not use zones var.
filename = config.get("zone_list")
if filename:
    # remove newlines from entries in zone filename using list comprehension
    zone_list = [line.rstrip() for line in open(filename)]
else:
    # if zone_list is not configured we require zones list in stack config
    zone_list = config.require_object("zones")

# In Akamai you have an account with a unique name and account id.
# Each account has one or more contracts each with an unique id.
# A contract will have multiple Akamai Products assigned to it, like EdgeDNS, ION etc.
# Within an account you have a main group and this group can have different subgroups.
# By default the main group name is combination of account name with contract id.
# Before we can start we need to have the approriate contract- and group id.
# This information can be found in the Akamai Control center under "contracts"
# can be found via  "akamai pm lg -f json" but set if via our config set.
group_name = config.require("group_name")

# let's first lookup the contract_id assigned to the API key coming from from .edgerc
# to select correct api user use "pulumi config set akamai:dnsSection|akamai:propertySection [section]"
# we should get same results as with "akamai pm lc --section [section]"
# we only need id and we're only using first entry from contacts list
# https://www.pulumi.com/docs/reference/pkg/akamai/getcontracts/
# return value will be a Pulumi Output object so we can build dependency graph
# we don't need it but just to show how to use Output.from_input()
contract_id = pulumi.Output.from_input(akamai.get_contracts()).contracts[0].contract_id

# now lookup group_id: https://www.pulumi.com/docs/reference/pkg/akamai/getgroup/
# using pulumi.Ouput object as we need to wait for results from that call when creating a zone
group_id = pulumi.Output.from_input(
    akamai.get_group(contract_id=contract_id, group_name=group_name).id
)

# our list of EdgeDNS objects to be created for this zone
# we're using a dict of dicts to make the lookup easy.
resource_list = {}
missed_records = []

# initiate a connection to OpenProvider API
# set set the environment vars in our venv/bin/activate file
# export OPENPROVIDER_USERNAME=username
# export OPENPROVIDER_PASSWORD=password
op = lookup_zones.OpenProvider()

for zone in zone_list:
    # first get all records, if zone is empty no need to create a zone
    all_records = op.get_zone(zone)

    if len(all_records) > 0:
        # we have some records, create new zone in EdgeDNS using the Pulumi Akamai Provider
        pulumi_zone = create_zone(zone, contract_id, group_id)

        for record in all_records:
            # now lets add all our records to this zone
            # first check if it's a record we support and value is not too long

            # we only need to process NS records if they are a subdomain in a zone, not a seperate zone
            if record["type"] == "NS" and record["name"] == zone:
                print(f"found NS record for TLD {record['name']}, skipping that one")
                break

            if (
                record["type"]
                in ["A", "CNAME", "TXT", "MX", "SRV", "AAAA", "CAA", "AKAMAICDN", "NS"]
                and len(record["value"]) < LIMIT
            ):

                # as not all records have a weight, make it empty or assign the value
                prio = record.get("prio", "")
                resource_name = "{}-{}{}".format(record["name"], record["type"], prio)

                # resource_name is unique key in our dict
                if resource_name in resource_list.keys():
                    # if we have already seen this resource, modify existing target value of the object
                    dns_record = resource_list[resource_name]
                    dns_record.append_target(record["value"])
                else:
                    # this is a new resource, let's create it and add to the dict
                    dns_record = DnsRecord(resource_name, pulumi_zone, record)
                    resource_list[resource_name] = dns_record

            elif len(record["value"]) > LIMIT:
                missed_records.append(record)
                pulumi.warn(
                    f"record too long for API: {record['name']} {record['type']}"
                )
    else:
        pulumi.warn("no records found to migrate")

# so we should have already created the zone, lets add the records which should have been normalized for EdgeDNS.
for resource in resource_list:
    resource_list[resource].create_record()

pulumi.export("missed_records", missed_records)