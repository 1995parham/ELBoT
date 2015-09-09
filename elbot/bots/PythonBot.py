# In The Name Of God
# ========================================
# [] File Name : PythonBot.py
#
# [] Creation Date : 26-07-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

from math import *
from elbot.core.AbstractBot import AbstractBot
from elbot.core.Message import Message
from elbot.core.BotFather import BotFather


class PythonBot(AbstractBot):
    bot_name = 'python'

    def run_query(self, message: Message, father: BotFather):
        father.send_message(text=str(PythonBot.interpreter(message.text)), chat_id=message.chat.id,
                            reply_to_message_id=message.message_id)

    @staticmethod
    def interpreter(query):
        safe_list = ['math', 'acos', 'asin', 'atan', 'atan2', 'ceil', 'cos', 'cosh', 'degrees', 'e', 'exp', 'fabs',
                     'floor',
                     'fmod', 'frexp', 'hypot', 'ldexp', 'log',
                     'log10', 'modf', 'pi', 'pow', 'radians', 'sin', 'sinh', 'sqrt', 'tan', 'tanh']
        # use the list to filter the local namespace
        safe_dict = dict([(k, locals().get(k)) for k in safe_list])
        safe_dict = dict([(k, globals().get(k)) for k in safe_list])
        # add any needed builtins back in.
        safe_dict['abs'] = abs
        safe_dict['len'] = len

        try:
            result = eval(query, {'__builtins__': None}, safe_dict)
        except Exception as exception:
            result = exception.args[0]
        return result


if __name__ == '__main__':
    print(PythonBot.interpreter('1 + 2'))
    print(PythonBot.interpreter('quit()'))
    print(PythonBot.interpreter('pi'))
