from petals_tensor.substrate.chain_functions import propose
from petals_tensor.substrate.tests.test_utils import MODEL_MEMORY_MB, MODEL_PATH, get_subnet_nodes_consensus_data, get_substrate_config



pre_subnet_data = {
	"path": list(MODEL_PATH.encode('ascii')),
	"memory_mb": MODEL_MEMORY_MB
}

vote_subnet_data = {
	"data": pre_subnet_data,
	"active": True
}

def propose_activate(n: int):
  substrate_config = get_substrate_config(n)
  subnet_nodes_consensus_data = get_subnet_nodes_consensus_data(5)
  print('subnet get_subnet_nodes_consensus_data: ', subnet_nodes_consensus_data)

  print('vote_subnet_data: ', vote_subnet_data)

  receipt = propose(
    substrate_config.interface,
    substrate_config.keypair,
    pre_subnet_data,
    get_subnet_nodes_consensus_data,
    "Activate"
  )
  print('propose receipt: ', receipt)
  return receipt