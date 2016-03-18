# In The Name Of God
# ========================================
# [] File Name : Update.py
#
# [] Creation Date : 27-08-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================

from elbot.core import Message


class Update:
    """
    This object represents an incoming update.
    :type update_id: int
    :type message: Message.Message
    """

    def __init__(self, update_id, message=None):
        self.update_id = update_id
        self.message = message


class UpdateDictDecoder:
    @staticmethod
    def decode(obj: dict) -> Update:
        return Update(update_id=int(obj['update_id']),
                      message=Message.MessageDictDecoder.decode(
                          obj['message']))
