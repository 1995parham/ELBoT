# In The Name Of God
# ========================================
# [] File Name : User.py
#
# [] Creation Date : 26-08-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import json


class User:
    """
    This object represents a Telegram user or bot.
    :type username: str
    :type first_name: str
    :type last_name: str
    :type id: int
    """

    def __init__(self, username, first_name, last_name, user_id):
        self.username = username
        self.first_name = first_name
        self.last_name = last_name
        self.id = user_id


class UserJSONEncoder(json.JSONEncoder):
    def default(self, obj: User):
        if isinstance(obj, User):
            return {
                'id': str(obj.id),
                'first_name': obj.first_name,
                'last_name': obj.last_name,
                'username': obj.username
            }
        else:
            raise TypeError("UserJsonEncoder got {} instead of User.".format(type(obj)))


class UserDictDecoder:
    @staticmethod
    def decode(obj: dict) -> User:
        return User(user_id=int(obj['id']), username=obj['username'], first_name=obj['first_name'],
                    last_name=obj['last_name'])
