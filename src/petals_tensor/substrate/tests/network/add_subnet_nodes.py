from substrateinterface import SubstrateInterface, Keypair
from petals_tensor.substrate.chain_functions import add_subnet_node, get_model_path_id
from petals_tensor.substrate.tests.test_utils import PEER_IDS, get_substrate_config


"""
This test requires a build with a subnet already initialized into the network pallet
"""

def test_add_subnet_nodes(count: int):
  print("adding test subnet nodes")
  substrate_config = get_substrate_config(0)
  subnet_id = get_model_path_id(
    substrate_config.interface,
    "bigscience/bloom-560m"
  )

  for n in range(count):
    substrate_config = get_substrate_config(n)
    add_subnet_node(substrate_config.interface, substrate_config.keypair, subnet_id, PEER_IDS[n], 1000e18)






if __name__ == "__main__":
  test_add_subnet_nodes(12)