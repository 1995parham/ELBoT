# In The Name Of God
# ========================================
# [] File Name : update.py
#
# [] Creation Date : 27-08-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================

from .message import Message, MessageDictDecoder


class Update:
    """
    This object represents an incoming update.
    :type update_id: int
    :type message: Message
    """

    def __init__(self, update_id: int, message: Message=None):
        self.id = update_id
        self.message = message


class UpdateDictDecoder:
    @staticmethod
    def decode(obj: dict) -> Update:
        return Update(update_id=int(obj['update_id']),
                      message=MessageDictDecoder.decode(
                          obj['message']))
