# our openprovider DNS migration script
# we're using ENV vars to login and generate a token
# after we have a token we retrieve records based on the a list of domains provided by the customer
# using postactivate from our virtualenv so to set some vars

import requests
import os
import json
import urllib3

# we  don't care about cert errors for now, migration only
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class OpenProvider:
    """Create a connection to the OpenProvider API using the /v1beta endpoint.
    Please provide valid username/password or use env var OPENPROVIDER_USERNAME and/or OPENPROVIDER_PASSWORD"""

    def __init__(
        self, username=None, password=None, url="https://api.openprovider.eu/v1beta"
    ):
        """Initializes the connection with the given username and password on the v1beta endpoint"""

        if not password:
            password = os.environ.get("OPENPROVIDER_PASSWORD")

        if not username:
            username = os.environ.get("OPENPROVIDER_USERNAME")

        self.username = username
        self.password = password
        self.url = url

        # Set up the API client using Session object so we can reuse certain values during request.
        self.session = requests.Session()

        # getting all kinds of SSL cert errors, skipping SSL check.
        self.session.verify = False
        self.session.headers["User-Agent"] = "openprovider.py/0.11.3"
        self.session.headers["Content-Type"] = "application/json"

        # during init fase we get token
        self._get_token()

    def _get_token(self):
        """ get an access token using provided username and password"""

        # define the URL for our login endpoint
        # https://docs.openprovider.com/doc/all
        token_url = f"{self.url}/auth/login"

        # our body which we convert into json during our request
        request_body = {}
        request_body["username"] = self.username
        request_body["password"] = self.password

        result = self.session.post(token_url, json.dumps(request_body))

        if result.ok:
            # convert json to dict and assign token, will fail if no answer.
            self.token = result.json()["data"]["token"]
            # print(f"token: {self.token}")
        else:
            raise Exception("username or password wrong")

    def get_zone(self, zone=None):
        """ return all availble zones or records of an individual zone"""

        # there is a limit on the numer of zones we can retrieve per call
        # https://docs.openprovider.com/doc/all#tag/ZoneService
        LIMIT = 500

        records_retrieved = 0
        all_zones = list()
        all_records = list()
        query_params = {"limit": LIMIT, "offset": 0}

        # if zones var has a value lookup that specific zone
        if zone:
            url = f"{self.url}/dns/zones/{zone}/records"
        else:
            url = f"{self.url}/dns/zones/"

        # we should have already have an access token use that as our Bearer token
        if self.token:
            self.session.headers["authorization"] = "Bearer " + self.token
        else:
            raise Exception("token missing")

        # the data.total field of the response provides the number of available zones
        # we're using a limit of LIMIT so we need to continue request zones until we reach data.total zones
        # we expect at least 1 record
        total_records = 1
        while records_retrieved < total_records:
            result = self.session.get(url, params=query_params)
            body = result.json()

            if result.ok:
                # only set total_records if not already set
                # total records is the end of our list so len of all zones should be total zones in the dns provider
                # both record types are using same structure so also showing total amount of results.
                if total_records == 1:
                    total_records = int(body["data"]["total"])

                if zone and total_records > 0:
                    # a zone has been defined, let's try to get some info but only if we have some result
                    # as we can get a 200 but with 0 records.
                    print(
                        f"looking up all records for zone: {zone} which has {total_records} records"
                    )
                    for record in body["data"]["results"]:
                        # just push DNS dict to all_records list
                        all_records.append(record)

                    records_retrieved = len(all_records)

                elif total_records > 0:
                    # let's get all the zones
                    # using some list comprehension and building list of all zone names
                    res = [zone["name"] for zone in result.json()["data"]["results"]]

                    # now add list of zones to our all_zones list so we get one list of only the zone names
                    all_zones.extend(res)

                    # numer of records retrieved is also becoming our offset
                    records_retrieved = len(all_zones)
                else:
                    # looks like we have nothing to return.
                    return all_zones

                query_params["offset"] = records_retrieved
            else:
                raise Exception("something went wrong")

        if zone:
            # this will return a list with our record dicts
            return all_records
        else:
            # this will return list with all zone names
            return all_zones


def main():
    op = OpenProvider()
    print(op.get_zone())


if __name__ == "__main__":
    main()
