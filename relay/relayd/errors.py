class ApiError(Exception):
    def __init__(self, code, msg, **extra):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.extra = extra
