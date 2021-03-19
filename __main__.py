"""A Python Pulumi program"""

import pulumi
import pulumi_akamai as akamai

# zone records are delivered in .csv format
# zone;name;type;target;weight;ttl
# grinwis.com;grinwis.com;MX;grinwis-nl.mail.protection.outlook.com;10;900
import csv

# let's define some static fields
ZONE = 0
NAME = 1
TYPE = 2
TARGET = 3
WEIGHT = 4
TTL = 5

# limit of the string length we can add to certain records via the API
# API is failing if record lenght is too long.
LIMIT = 250

# quick hack to solve issues where we have to put multiple records into one
# in a next version we should clean it up and make some nice zone/record objects etc.
class DnsRecord:
    def __init__(self, resource_name, zone, record):

        self.resource_name = resource_name
        self.name = record[NAME]
        self.type = record[TYPE]
        self.targets = []

        # we have seen records with have some extra empty fields.
        # last field is always ttl field and should always be set so using that one.
        self.ttl = int(row[len(row) - 1] or 3600)
        self.zone = zone

        if record[WEIGHT]:
            self.weight = int(record[WEIGHT])

        # print(f"adding: {record[TARGET]}")
        self.targets.append(record[TARGET])

    def append_target(self, target):
        # we can extend this method to so some more checks like look for double entries etc.
        self.targets.append(target)

    def create_record(self):
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
                priority=self.weight,
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
                priority=self.weight,
            )


def create_zone(zone, contract_id, group_id):
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


# A pulumi project can have different stack which it's own state.
# select file with zones from stack config
# "pulumi config set filename zones.csv"
config = pulumi.Config()
filename = config.require("filename")

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

# our zones dict will contain key of unique zones and list of DnsRecord objects.
zones = {}
missed_records = []
with open(filename, newline="") as csv_file:
    csv_reader = csv.reader(csv_file, delimiter=";")
    for row in csv_reader:
        # only try to create a zone if not already confgured
        if row[ZONE] and row[ZONE] not in zones.keys():
            my_zone = create_zone(row[ZONE], contract_id, group_id)
            # intialize our zones with empty records list
            zones[row[ZONE]] = []

        # for now only add these common records and more records can be added but double check needed fields
        # https://www.pulumi.com/docs/reference/pkg/akamai/dnsrecord/
        # https://registry.terraform.io/providers/akamai/akamai/latest/docs/resources/dns_record
        # we can skip SOA and NS records as they are created during the create_zone call
        # during testing some really long TXT records failed to be applied so we're setting a limit of 250,
        if (
            row[TYPE] in ["A", "CNAME", "TXT", "MX", "SRV", "AAAA", "CAA"]
            and len(row[TARGET]) < LIMIT
        ):
            record_modified = False

            # some records have the same name but we added weight as MX needs three different resource records.
            resource_name = "{}-{}{}".format(row[NAME], row[TYPE], row[WEIGHT])

            # check if we have seen this record DnsRecord object before
            # if so, we only need to append the target to targets list field of this object
            # during our tests no need to change format of the txt input, pulumi is taking care of that
            for record in zones[row[ZONE]]:
                if record.resource_name == resource_name:
                    record.append_target(row[TARGET])
                    record_modified = True
                    break  # no need to look futher, break out of the for loop.

            # if it's a new record, add it to the targets object list.
            if record_modified == False:
                # print(f"adding record: {row}")
                record = DnsRecord(resource_name, my_zone, row)
                zones[row[ZONE]].append(record)
        else:
            if len(row[TARGET]) > LIMIT:
                pulumi.warn(f"record too long for API: {row[NAME]} {row[TYPE]}")
                missed_records.append(row)

# zones should have be created, let's create some dnsrecords
for zone in zones:
    for records in zones[zone]:
        my_record = records.create_record()
        # pulumi.export("record", my_record)

pulumi.export("missed_records", missed_records)