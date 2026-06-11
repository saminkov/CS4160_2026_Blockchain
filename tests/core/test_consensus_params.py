from __future__ import annotations

import pytest

from blockchain.core.consensus_params import (
    ConsensusParams,
    community_id_from_group_id,
    load_group_id,
    load_member_pubkeys,
)
from blockchain.core.entities import HASH_SIZE
from blockchain.core.hashing import block_hash, tx_hash, txs_hash


class TestReward:
    def test_height_zero_is_zero(self) -> None:
        params = ConsensusParams.default()
        assert params.reward(0) == 0

    def test_height_one_full_reward(self) -> None:
        params = ConsensusParams.default()
        assert params.reward(1) == params.initial_reward_coins * params.coin

    def test_halving_at_interval(self) -> None:
        params = ConsensusParams.default()
        interval = params.halving_interval
        assert params.reward(interval) == (params.initial_reward_coins // 2) * params.coin
        assert params.reward(interval * 2) == (params.initial_reward_coins // 4) * params.coin

    def test_reward_zero_after_final_halving(self) -> None:
        params = ConsensusParams.default()
        height = params.initial_reward_coins.bit_length() * params.halving_interval
        assert params.reward(height) == 0


class TestGroupRegistration:
    def test_group_id_loaded_from_registration_file(self) -> None:
        params = ConsensusParams.default()
        assert params.group_id == load_group_id()

    def test_community_id_derived_from_group_id(self) -> None:
        params = ConsensusParams.default()
        assert params.community_id == community_id_from_group_id(params.group_id)
        assert params.community_id == community_id_from_group_id(load_group_id())

    def test_member_pubkeys_loaded_from_key_files(self) -> None:
        params = ConsensusParams.default()
        assert params.member_pubkeys == load_member_pubkeys()


class TestParamsHash:
    def test_stable_for_default_params(self) -> None:
        params = ConsensusParams.default()
        first = params.params_hash()
        second = params.params_hash()
        assert first == second
        assert len(first) == HASH_SIZE

    def test_changes_when_difficulty_changes(self) -> None:
        default = ConsensusParams.default()
        other = ConsensusParams(
            difficulty_bits=default.difficulty_bits + 1,
            coin=default.coin,
            premine_total_coins=default.premine_total_coins,
            premine_per_member_coins=default.premine_per_member_coins,
            initial_reward_coins=default.initial_reward_coins,
            halving_interval=default.halving_interval,
            coinbase_maturity=default.coinbase_maturity,
            timestamp_tolerance_seconds=default.timestamp_tolerance_seconds,
            genesis_timestamp=default.genesis_timestamp,
            genesis_nonce=default.genesis_nonce,
            genesis_headline=default.genesis_headline,
            genesis_coinbase_signature=default.genesis_coinbase_signature,
            group_id=default.group_id,
            community_id=default.community_id,
            member_pubkeys=default.member_pubkeys,
        )
        assert default.params_hash() != other.params_hash()


class TestBuildGenesis:
    def test_bitcoin_homage_header_fields(self) -> None:
        params = ConsensusParams.default()
        genesis = params.build_genesis()
        header = genesis.block.header
        assert header.prev_hash == b"\x00" * HASH_SIZE
        assert header.timestamp == params.genesis_timestamp
        assert header.difficulty == params.difficulty_bits
        assert header.nonce == params.genesis_nonce

    def test_genesis_hash_commitments(self) -> None:
        genesis = ConsensusParams.default().build_genesis()
        coinbase = genesis.block.transactions[0]
        coinbase_hash = tx_hash(coinbase)
        assert genesis.block.header.txs_hash == txs_hash((coinbase,))
        assert genesis.block_hash == block_hash(genesis.block.header)
        assert all(utxo.outpoint.txid == coinbase_hash for utxo in genesis.premine_utxos)

    def test_premine_utxos(self) -> None:
        params = ConsensusParams.default()
        genesis = params.build_genesis()
        assert len(genesis.premine_utxos) == len(params.member_pubkeys)
        for index, utxo in enumerate(genesis.premine_utxos):
            assert utxo.outpoint.index == index
            assert utxo.amount == params.premine_amount_base_units()
            assert utxo.height_created == 0
            assert utxo.is_coinbase is True
            assert utxo.recipient_pubkey == params.member_pubkeys[index]

    def test_genesis_coinbase_includes_headline(self) -> None:
        params = ConsensusParams.default()
        coinbase = params.build_genesis().block.transactions[0]
        assert params.genesis_headline in coinbase.data

    def test_deterministic_across_calls(self) -> None:
        params = ConsensusParams.default()
        first = params.build_genesis()
        second = params.build_genesis()
        assert first.block_hash == second.block_hash
        assert first.premine_utxos == second.premine_utxos

    def test_rejects_unsorted_member_keys(self) -> None:
        params = ConsensusParams.default()
        reversed_keys = tuple(reversed(params.member_pubkeys))
        with pytest.raises(ValueError, match="sorted"):
            ConsensusParams(
                difficulty_bits=params.difficulty_bits,
                coin=params.coin,
                premine_total_coins=params.premine_total_coins,
                premine_per_member_coins=params.premine_per_member_coins,
                initial_reward_coins=params.initial_reward_coins,
                halving_interval=params.halving_interval,
                coinbase_maturity=params.coinbase_maturity,
                timestamp_tolerance_seconds=params.timestamp_tolerance_seconds,
                genesis_timestamp=params.genesis_timestamp,
                genesis_nonce=params.genesis_nonce,
                genesis_headline=params.genesis_headline,
                genesis_coinbase_signature=params.genesis_coinbase_signature,
                group_id=params.group_id,
                community_id=params.community_id,
                member_pubkeys=reversed_keys,
            )
