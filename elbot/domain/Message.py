# In The Name Of God
# ========================================
# [] File Name : Message.py
#
# [] Creation Date : 27-08-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================

import datetime

from elbot.domain import GroupChat
from elbot.domain import User


class Message:
    """
    This object represents a message.
    :type message_id: int
    :type src: User.User
    :type date: datetime.datetime
    :type chat: User.User or GroupChat.GroupChat
    :type text: str
    :type forward_from: User.User
    :type forward_date: datetime.datetime
    :type reply_to_message: Message
    """

    def __init__(self, message_id, src, date, chat, text='', forward_from=None,
                 forward_date=None,
                 reply_to_message=None):
        self.message_id = message_id
        self.src = src
        self.date = date
        self.chat = chat
        self.text = text
        self.forward_from = forward_from
        self.forward_date = forward_date
        self.reply_to_message = reply_to_message


class MessageDictDecoder:
    @staticmethod
    def decode(obj: dict) -> Message:
        # Message.text
        text = obj.get('text', '')

        # Message.forward_from
        if obj.get('forward_from', None):
            forward_from = User.UserDictDecoder.decode(obj['forward_from'])
        else:
            forward_from = None

        # Message.forward_date
        if obj.get('forward_date', None):
            forward_date = datetime.datetime.fromtimestamp(obj['forward_date'])
        else:
            forward_date = None

        # Message.reply_to_message
        if obj.get('reply_to_message', None):
            reply_to_message = MessageDictDecoder.decode(
                obj['reply_to_message'])
        else:
            reply_to_message = None

        # Message.chat
        type = obj['chat']['type']
        if type == 'private':
            chat = User.UserDictDecoder.decode(obj['chat'])
        elif type == 'group':
            chat = GroupChat.GroupChatDictDecoder.decode(obj['chat'])

        return Message(message_id=int(obj['message_id']),
                       src=User.UserDictDecoder.decode(obj['from']),
                       date=datetime.datetime.fromtimestamp(obj['date']),
                       chat=chat, text=text,
                       forward_from=forward_from, forward_date=forward_date,
                       reply_to_message=reply_to_message)