class EPOServiceError(Exception):
    pass

class EPOTimeoutError(EPOServiceError):
    pass

class EPOConnectionError(EPOServiceError):
    pass

class EPOUpstreamError(EPOServiceError):
    pass

class EPORateLimitError(EPOServiceError):
    pass

class AgentInternalError(Exception):
    pass

class AgentRuntimeLimitError(AgentInternalError):
    pass
