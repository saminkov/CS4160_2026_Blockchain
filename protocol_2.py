from ipv8.messaging.payload_dataclass import VariablePayload

from protocol import COMMUNITY_ID, SERVER_PUBLIC_KEY

__all__ = [
    "COMMUNITY_ID",
    "SERVER_PUBLIC_KEY",
    "GroupRegistrationPayload",
    "GroupRegistrationResponsePayload",
    "ChallengeRequestPayload",
    "ChallengeResponsePayload",
    "SignatureBundlePayload",
    "RoundResultPayload",
    "PeerSignaturePayload",
]


class GroupRegistrationPayload(VariablePayload):
    msg_id = 1
    format_list = ["varlenH", "varlenH", "varlenH"]
    names = ["member1_key", "member2_key", "member3_key"]


class GroupRegistrationResponsePayload(VariablePayload):
    msg_id = 2
    format_list = ["?", "varlenHutf8", "varlenHutf8"]
    names = ["success", "group_id", "message"]


class ChallengeRequestPayload(VariablePayload):
    msg_id = 3
    format_list = ["varlenHutf8"]
    names = ["group_id"]


class ChallengeResponsePayload(VariablePayload):
    msg_id = 4
    format_list = ["varlenH", "q", "d"]
    names = ["nonce", "round_number", "deadline"]


class SignatureBundlePayload(VariablePayload):
    msg_id = 5
    format_list = ["varlenHutf8", "q", "varlenH", "varlenH", "varlenH"]
    names = ["group_id", "round_number", "sig1", "sig2", "sig3"]


class RoundResultPayload(VariablePayload):
    msg_id = 6
    format_list = ["?", "q", "q", "varlenHutf8"]
    names = ["success", "round_number", "rounds_completed", "message"]


class PeerSignaturePayload(VariablePayload):
    """Peer-to-peer: non-submitter sends its signature to the designated submitter."""
    msg_id = 7
    format_list = ["varlenH", "q", "varlenH"]
    names = ["nonce", "round_number", "signature"]
