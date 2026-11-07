from collections import namedtuple

RawReply = namedtuple("RawReply", "body ctype headers")      # a read that answers with bytes, not JSON


class ApiError(Exception):
    def __init__(self, code, msg, **extra):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.extra = extra
