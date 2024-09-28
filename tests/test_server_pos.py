import random
import pytest

from hivemind.dht.routing import DHTID, RoutingTable
from hivemind.utils.networking import LOCALHOST

def test_routing_table_basic__delitem__():
    node_id = DHTID.generate()
    routing_table = RoutingTable(node_id, bucket_size=20, depth_modulo=5)
    added_nodes = []

    for phony_neighbor_port in random.sample(range(10000), 100):
        phony_id = DHTID.generate()
        routing_table.add_or_update_node(phony_id, f"{LOCALHOST}:{phony_neighbor_port}")
        assert phony_id in routing_table
        assert f"{LOCALHOST}:{phony_neighbor_port}" in routing_table
        assert routing_table[phony_id] == f"{LOCALHOST}:{phony_neighbor_port}"
        assert routing_table[f"{LOCALHOST}:{phony_neighbor_port}"] == phony_id
        added_nodes.append(phony_id)

    assert routing_table.buckets[0].lower == DHTID.MIN and routing_table.buckets[-1].upper == DHTID.MAX
    for bucket in routing_table.buckets:
        assert len(bucket.replacement_nodes) == 0, "There should be no replacement nodes in a table with 100 entries"
    assert 3 <= len(routing_table.buckets) <= 10, len(routing_table.buckets)

    random_node = random.choice(added_nodes)
    assert routing_table.get(node_id=random_node) == routing_table[random_node]
    dummy_node = DHTID.generate()
    assert (dummy_node not in routing_table) == (routing_table.get(node_id=dummy_node) is None)

    for node in added_nodes:
        found_bucket_index = routing_table.get_bucket_index(node)
        for bucket_index, bucket in enumerate(routing_table.buckets):
            if bucket.lower <= node < bucket.upper:
                break
        else:
            raise ValueError("Naive search could not find bucket. Universe has gone crazy.")
        assert bucket_index == found_bucket_index

    for phony_id in added_nodes:
        assert phony_id in routing_table
        routing_table.__delitem__(phony_id)
        assert phony_id not in routing_table