# In The Name Of God
# ========================================
# [] File Name : group.py
#
# [] Creation Date : 26-08-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
import json


class GroupChat:
    """
    This object represents a group chat.
    :type title: str
    :type group_id: int
    """

    def __init__(self, title, group_id):
        self.title = title
        self.id = group_id


class GroupChatJSONEncoder(json.JSONEncoder):
    def default(self, obj: GroupChat):
        if isinstance(obj, GroupChat):
            return {
                'id': str(obj.id),
                'title': obj.title
            }
        else:
            raise TypeError(
                "GroupChatJsonEncoder got {} instead of GroupChat.".format(
                    type(obj)))


class GroupChatDictDecoder:
    @staticmethod
    def decode(obj: dict) -> GroupChat:
        return GroupChat(group_id=int(obj['id']), title=obj['title'])
