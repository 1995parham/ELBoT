# In The Name Of God
# ========================================
# [] File Name : ReplyKeyboardMarkup.py
#
# [] Creation Date : 09-09-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import json


class ReplyKeyboardMarkup:
    """
    This object represents a custom keyboard with reply options (see Introduction to bots for details and examples).
    :type keyboard: list[list[str]]
    :type resize_keyboard: bool
    :type one_time_keyboard: bool
    :type selective: bool
    """

    def __init__(self, keyboard, resize_keyboard=False, one_time_keyboard=False, selective=False):
        self.keyboard = keyboard
        self.resize_keyboard = resize_keyboard
        self.one_time_keyboard = one_time_keyboard
        self.selective = selective


class ReplyKeyboardMarkdownJSONEncoder(json.JSONEncoder):
    def default(self, obj: ReplyKeyboardMarkup):
        if isinstance(obj, ReplyKeyboardMarkup):
            return {
                'keyboard': json.dumps(obj.keyboard),
                'resize_keyboard': 'true' if obj.resize_keyboard else 'false',
                'one_time_keyboard': 'true' if obj.one_time_keyboard else 'false',
                'selective': 'true' if obj.selective else 'false'
            }
        else:
            raise TypeError("UserJsonEncoder got {} instead of ReplyKeyboardMarkup.".format(type(obj)))
