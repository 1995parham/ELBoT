# In The Name Of God
# ========================================
# [] File Name : GroupChat
#
# [] Creation Date : 26-08-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import json


class GroupChat:
    """
    This object represents a group chat.
    :type title: str
    :type group_id: int
    """

    def __init__(self, title, group_id):
        self.title = title
        self.group_id = group_id


class GroupChatJSONEncoder(json.JSONEncoder):
    def default(self, obj: GroupChat):
        if obj is GroupChat:
            return {
                'id': obj.group_id,
                'title': obj.title
            }
        else:
            raise TypeError("UserJsonEncoder got {} instead of User.".format(type(obj)))


class GroupChatDictDecoder:
    @staticmethod
    def decode(obj: dict) -> GroupChat:
        return GroupChat(group_id=obj['id'], title=obj['title'])
