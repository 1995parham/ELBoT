import logging

elbot_log_format = (
    'ELBot: [%(asctime)s] %(levelname)-8s %(name)-12s %(thread)-8s %(message)s'
)

elbot_log_formatter = logging.Formatter(elbot_log_format)
elbot_log_handler = logging.StreamHandler()
elbot_log_handler.setLevel(logging.DEBUG)
elbot_log_handler.setFormatter(elbot_log_formatter)
logger = logging.getLogger('ELBot')
logger.setLevel(logging.DEBUG)
logger.addHandler(elbot_log_handler)
