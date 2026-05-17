from ipv8.messaging.payload_dataclass import VariablePayload

# COMMUNITY_ID = bytes.fromhex("2c1cc6e35ff484f99ebdfb6108477783c0102881")
# SERVER_PUBLIC_KEY = bytes.fromhex(
#     "4c69624e61434c504b3a86b23934a28d669c390e2d1fc0b0870706c4591cc0cb1"
#     "78bc5a811da6d87d27ef319b2638ef60cc8d119724f4c53a1ebfad919c3ac4136"
#     "c501ce5c09364e0ebb"
# )


COMMUNITY_ID = bytes.fromhex("4c61623247726f75705369676e696e6732303236")
SERVER_PUBLIC_KEY = bytes.fromhex(
    "4c69624e61434c504b3a82e33614a342774e084af80" \
    "835838d6dbdb64a537d3ddb6c1d82011a7f101553cd" \
    "a40cf5fa0e0fc23abd0a9c4f81322282c5b34566f6b" \
    "8401f5f683031e60c96"
)       

SOFI_KEY_PUBLIC_KEY = bytes.fromhex(
    "4c69624e61434c504b3a"
    "bc883b1f380f73866edab7394da3c81dee556f7ffe6f61fcbb38a3dc37907c1b"
    "c6d0618a999c68dfe6dd9160f75e555d8f9c42b55d1055c98ee3b60fc991214f"
)
POLLY_KEY_PUBLIC_KEY = bytes.fromhex(
    "4c69624e61434c504b3a"
    "1ef20d22fc494975ba5c397f7c8142bda1924e4e6badbe9057330475dda4726f"
    "775baf2b168865613eca46d76e60df19b9be84dcf3dc2f28f25c546a8d3a647d"
)
class SubmissionPayload(VariablePayload):
    msg_id = 1
    format_list = ["varlenHutf8", "varlenHutf8", "q"]
    names = ["email", "github_url", "nonce"]


class ResponsePayload(VariablePayload):
    msg_id = 2
    format_list = ["?", "varlenHutf8"]
    names = ["success", "message"]
