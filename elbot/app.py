# In The Name Of God
# ========================================
# [] File Name : app.py
#
# [] Creation Date : 11-10-2016
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
import time
import requests
import json

from .domain.update import Update, UpdateDictDecoder


class ELBot:
    def __init__(self, hash_id):
        self.hash_id = hash_id
        self.base_url = 'https://api.telegram.org/bot' + self.hash_id + '/'

    def get_updates(self, offset: int = 0, limit: int = 0,
                    timeout: int = 0) -> [Update]:
        """
        Use this method to receive incoming updates using long polling.
        An Array of Update objects is returned.
        :param offset: Identifier of the first update to be returned.
                       Must be greater by one than the highest among the
                       identifiers of previously received updates.
                       By default, updates starting with the earliest
                       unconfirmed update are returned.
                       An update is considered confirmed as soon as getUpdates
                       is called with an offset higher than
                       its update_id.
        :param limit: Limits the number of updates to be retrieved.
                      Values between 1—100 are accepted. Defaults to 100
        :param timeout: Timeout in seconds for long polling. Defaults to 0,
                        i.e. usual short polling
        :return: list[Update]
        """
        updates = []
        params = {}
        if offset != 0:
            params['offset'] = offset
        if limit != 0:
            params['limit'] = limit
        if timeout != 0:
            params['timeout'] = timeout
        response = requests.get(url=self.base_url + 'getUpdates',
                                params=params)
        response = json.loads(response.text)
        if response['ok']:
            for obj in response['result']:
                update = UpdateDictDecoder.decode(obj)
                updates.append(update)
        else:
            print("We get error at {}".format(response['result']))
        return updates

    def run(self):
        update_id = 0
        while True:
            try:
                updates = self.get_updates(offset=update_id)
                if len(updates) != 0:
                    update_id = updates[-1].update_id + 1
                for update in updates:
                    message = update.message
                    print(message.text)
            except Exception as ex:
                print("Error: {}".format(ex))
                raise ex
        time.sleep(10)
