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


def create_record(record, zone):
    # let's create the DNS records, every record has it's own setup of requirements
    # https://www.pulumi.com/docs/reference/pkg/akamai/dnsrecord/
    # we should get an array with the following fields
    # zone;name;record;target;priority;ttl
    # we use pulumi output object as we need to wait for the promise from the DnsZone call

    if len(record) != 6:
        pulumi.warn("something wrong, not creating record: {}".format(record))
        return ()

    # set default ttl in case it's not configured or just return, check with customer as some records don't have a TTL
    ttl = record[TTL] or 3600

    # multiple targets are probably split with a ',' but need to double check this!
    targets = record[TARGET].split(",")

    # our unique resource name
    resource_name = "{}-{}-{}".format(record[NAME], record[TYPE], record[TARGET])

    # making use of the pulumi Output object zone.zone for the zone so we create a depency between the zone resource and dns record
    # we need to wait until resource has been created.
    if row[TYPE] not in ["SRV", "MX"]:
        return akamai.DnsRecord(
            resource_name,
            recordtype=record[TYPE],
            ttl=int(ttl),
            zone=zone.zone,
            name=record[NAME],
            targets=targets,
        )

    if row[TYPE] == "SRV":
        # this is our special SRV record, we need to add priority, weight, port, target
        # 100 1 5061 sipfed.online.lync.com.
        # in the .csv it's configured like this
        # 1 443 sipdir.online.lync.com;100
        srv_record = record[TARGET].split()

        return akamai.DnsRecord(
            resource_name,
            recordtype=record[TYPE],
            ttl=int(ttl),
            zone=zone.zone,
            name=record[NAME],
            priority=int(record[WEIGHT]),
            weight=int(srv_record[0]),
            port=int(srv_record[1]),
            targets=srv_record[2].split(),
        )

    # MX records need seperate priority field
    if row[TYPE] == "MX":
        return akamai.DnsRecord(
            resource_name,
            recordtype=record[TYPE],
            ttl=int(ttl),
            zone=zone.zone,
            name=record[NAME],
            targets=targets,
            priority=int(record[WEIGHT]),
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

zones = []
with open(filename, newline="") as csv_file:
    csv_reader = csv.reader(csv_file, delimiter=";")
    for row in csv_reader:
        # only try to create a zone if not already confgured
        if row[ZONE] and row[ZONE] not in zones:
            my_zone = create_zone(row[ZONE], contract_id, group_id)
            zones.append(row[ZONE])

        # for now only add these common records and more records can be added but double check needed fields
        # https://www.pulumi.com/docs/reference/pkg/akamai/dnsrecord/
        # we can skip SOA and NS records as they are created during the create_zone call
        if row[TYPE] in ["A", "CNAME", "TXT", "MX", "SRV"]:
            my_record = create_record(row, my_zone)

# pulumi.export("zone", my_zone)