from ipv8.messaging.payload_dataclass import VariablePayload

COMMUNITY_ID = bytes.fromhex("2c1cc6e35ff484f99ebdfb6108477783c0102881")
SERVER_PUBLIC_KEY = bytes.fromhex(
    "4c69624e61434c504b3a86b23934a28d669c390e2d1fc0b0870706c4591cc0cb1"
    "78bc5a811da6d87d27ef319b2638ef60cc8d119724f4c53a1ebfad919c3ac4136"
    "c501ce5c09364e0ebb"
)


class SubmissionPayload(VariablePayload):
    msg_id = 1
    format_list = ["varlenHutf8", "varlenHutf8", "q"]
    names = ["email", "github_url", "nonce"]


class ResponsePayload(VariablePayload):
    msg_id = 2
    format_list = ["?", "varlenHutf8"]
    names = ["success", "message"]
