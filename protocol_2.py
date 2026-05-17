from ipv8.messaging.payload_dataclass import VariablePayload

from protocol import COMMUNITY_ID, SERVER_PUBLIC_KEY, SOFI_KEY_PUBLIC_KEY, POLLY_KEY_PUBLIC_KEY

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
    "SignatureRequestPayload",
    "StartPollingPayload",
    "GroupIdPayload",
    "GroupIdAckPayload",
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


class SignatureRequestPayload(VariablePayload):
    """Submitter → others: forward challenge nonce so they can sign it."""
    msg_id = 8
    format_list = ["varlenH", "q"]
    names = ["nonce", "round_number"]


class StartPollingPayload(VariablePayload):
    """Submitter K → peer (rounds_completed % 3) + 1: hand off polling."""
    msg_id = 9
    format_list = ["q"]
    names = ["round_number"]


class GroupIdPayload(VariablePayload):
    """Peer 1 → peers 2 & 3: share the registered group_id."""
    msg_id = 10
    format_list = ["varlenHutf8"]
    names = ["group_id"]


class GroupIdAckPayload(VariablePayload):
    """Peers 2 & 3 → peer 1: acknowledge receipt of group_id."""
    msg_id = 11
    format_list = ["varlenHutf8"]
    names = ["group_id"]
