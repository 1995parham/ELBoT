#!/usr/bin/env python3

from elbot.app import ELBot
from elbot.domain.message import Message


elbot = ELBot('152389518:AAHW5iTAkK-3UqHV70PGd--bLQDdv3SnyQk')


@elbot.message(r'^/ser*')
def run_query():
    print("seriously")

elbot.run()
