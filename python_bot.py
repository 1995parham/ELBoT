#!/usr/bin/env python3

from elbot.app import ELBot
from elbot.domain.message import Message


elbot = ELBot('152389518:AAHW5iTAkK-3UqHV70PGd--bLQDdv3SnyQk')


@elbot.message(r'^/python')
def python_handler(message: Message):
    elbot.send_message(
        text=str(interpret(message.text.split(' ', maxsplit=1)[1])),
        chat_id=message.chat.id,
        reply_to_message_id=message.id
    )


def interpret(query):
    safe_list = ['math', 'acos', 'asin', 'atan', 'atan2', 'ceil', 'cos',
                 'cosh', 'degrees', 'e', 'exp', 'fabs', 'floor',
                 'fmod', 'frexp', 'hypot', 'ldexp', 'log',
                 'log10', 'modf', 'pi', 'pow', 'radians', 'sin',
                 'sinh', 'sqrt', 'tan', 'tanh']
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

elbot.run()
