"""MQTT topic constants for Instagram's realtime infrastructure."""

# Topic IDs (used in PUBLISH packets as UTF-8 topic strings)
PUBSUB = 88
SEND_MESSAGE = 132
SEND_MESSAGE_RESPONSE = 133
SUB_IRIS = 134
SUB_IRIS_RESPONSE = 135
MESSAGE_SYNC = 146
REALTIME_SUB = 149
REGION_HINT = 150
LS_RESP = 179
RS_REQ = 244
RS_RESP = 245
RTC_LOG = 274
PP = 34

# Human-readable names for logging
TOPIC_NAMES: dict[str, str] = {
    "34": "/pp",
    "88": "/pubsub",
    "132": "/ig_send_message",
    "133": "/ig_send_message_response",
    "134": "/ig_sub_iris",
    "135": "/ig_sub_iris_response",
    "146": "/ig_message_sync",
    "149": "/ig_realtime_sub",
    "150": "/t_region_hint",
    "179": "/ls_resp",
    "244": "/rs_req",
    "245": "/rs_resp",
    "274": "/t_rtc_log",
}

# Topics to subscribe to in CONNECT payload
SUBSCRIBE_TOPICS = [
    PUBSUB,
    SUB_IRIS_RESPONSE,
    RS_REQ,
    REALTIME_SUB,
    REGION_HINT,
    RS_RESP,
    RTC_LOG,
    SEND_MESSAGE_RESPONSE,
    MESSAGE_SYNC,
    LS_RESP,
    PP,
]
