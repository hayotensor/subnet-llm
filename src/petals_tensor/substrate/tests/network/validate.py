import asyncio
from dataclasses import asdict, is_dataclass
import signal
import time
import multiprocessing as mp
from substrateinterface import SubstrateInterface, Keypair
from petals_tensor.substrate.chain_data import RewardsData
from petals_tensor.substrate.config import SubstrateConfigCustom
from petals_tensor.substrate.tests.network.add_subnet_nodes import test_add_subnet_nodes
from petals_tensor.substrate.tests.test_utils import LOCAL_URL, MODEL_PATH, PEER_IDS, get_subnet_nodes_consensus_data, get_substrate_config, BLOCK_SECS
from signal import signal, SIGPIPE, SIG_DFL  
signal(SIGPIPE,SIG_DFL) 


"""
This test requires a build with a subnet already initialized into the network pallet
"""
import threading
from petals_tensor.substrate.chain_functions import get_submittables, get_min_required_model_consensus_submit_epochs, get_model_path_id, attest, get_epoch_length, get_model_activated, get_model_data, get_model_path_id, get_rewards_submission, get_rewards_validator, validate

from hivemind.utils import get_logger

logger = get_logger(__name__)

PEERS_LENGTH = 5
# # python src/petals_tensor/substrate/tests/network/validate.py

class TestConsensus(threading.Thread):
  """
  Houses logic for validating and attesting consensus data per epochs for rewards

  This can be ran before or during a model activation.

  If before, it will wait until the subnet is successfully voted in, if the proposal to initialize the subnet fails,
  it will not stop running.

  If after, it will begin to validate and or attest epochs
  """
  def __init__(self, path: str, account_id: str, phrase: str, url: str):
    super().__init__()
    self.subnet_id = None # Not required in case of not initialized yet
    self.path = path
    self.account_id = account_id
    self.subnet_accepting_consensus = False
    self.subnet_node_eligible = False
    self.subnet_initialized = 9223372036854775807 # max int
    self.last_validated_or_attested_epoch = 0

    self.substrate_config = SubstrateConfigCustom(phrase, url)

    # blockchain constants
    self.epoch_length = int(str(get_epoch_length(self.substrate_config.interface)))
    self.min_required_model_consensus_submit_epochs = get_min_required_model_consensus_submit_epochs(self.substrate_config.interface)

  # def run(self):
  #   epoch = 0
  #   while True:
  #     time.sleep(BLOCK_SECS)
  #     epoch += 1
  #     logger.info("Epoch: %s " % epoch)

  def run(self):
    while True:
      try:
        print("starting run() in try")
        block_hash = self.substrate_config.interface.get_block_hash()
        block_number = self.substrate_config.interface.get_block_number(block_hash)

        epoch = int(block_number / self.epoch_length)
        logger.info("Epoch: %s " % epoch)

        logger.info("Last Validated Epoch: %s " % self.last_validated_or_attested_epoch)

        # skip if already validated or attested epoch
        if epoch <= self.last_validated_or_attested_epoch:
          logger.info("Already completed epoch: %s, waiting for the next " % epoch)
          time.sleep(BLOCK_SECS)
          continue

        next_epoch_start_block = self.epoch_length + (block_number - (block_number % self.epoch_length))
        
        # Ensure subnet is activated
        if self.subnet_accepting_consensus == False:
          activated = self._activate_subnet()
          if activated == True:
            continue
          else:
            # Sleep until voting is complete
            time.sleep(BLOCK_SECS)
            continue
        """
        Is subnet node initialized and eligible to submit consensus
        """
        # subnet is eligible to accept consensus
        # check if we are submittable
        # in order to be submittable:
        # - Must stake onchain
        # - Must be Submittable subnet node class
        if self.subnet_node_eligible == False:
          submittable_nodes = get_submittables(self.substrate_config.interface, self.subnet_id)
          print('submittable_nodes', submittable_nodes)

          for node_set in submittable_nodes:
            if node_set[0] == self.account_id:
              self.subnet_node_eligible = True
              break
          
          if self.subnet_node_eligible == False:
            logger.info("Node not eligible for consensus, sleeping until next epoch")
            time.sleep(BLOCK_SECS)
            continue

        # is epoch submitted yet

        # is validator?
        validator = self._get_validator(epoch)

        if validator == None:
          logger.info("Validator not chosen for epoch %s yet, checking next block" % epoch)
          time.sleep(BLOCK_SECS)
          continue
        else:
          logger.info("Validator for epoch %s is %s" % (epoch, validator))

        is_validator = validator == self.account_id
        if is_validator:
          logger.info("We're the chosen validator for epoch %s, validating and auto-attesting..." % epoch)
          # check if validated 
          validated = False
          if validated is False:
            self.validate()
            self.last_validated_or_attested_epoch = epoch

          # continue to next epoch, no need to attest
          time.sleep(BLOCK_SECS)
          continue

        # we are not validator, we must attest or not attest
        # wait until validated by epochs chosen validator

        # get epoch before waiting for validator to validate to ensure we don't get stuck 
        initial_epoch = epoch
        attestation_complete = False
        logger.info("Starting attestation check")
        while True:
          # wait for validator on every block
          time.sleep(BLOCK_SECS)
          block_hash = self.substrate_config.interface.get_block_hash()
          block_number = self.substrate_config.interface.get_block_number(block_hash)

          epoch = int(block_number / self.epoch_length)
          logger.info("Epoch in Attestation: %s " % epoch)

          # If we made it to the next epoch, break
          # This likely means the chosen validator never submitted consensus data
          if epoch > initial_epoch:
            logger.info("Validator didn't submit consensus data, moving to the next epoch: %s" % epoch)
            break

          result = self.attest(epoch)
          print("attestation result is: %s" % result)
          if result == None:
            # If None, still waiting for validator to submit data
            continue
          else:
            # successful attestation, break and go to next epoch
            self.last_validated_or_attested_epoch = epoch
            break
      except Exception as e:
        logger.error("Consensus Error: %s" % e)

  def validate(self):
    print("validate")
    """Get rewards data and submit consensus"""
    consensus_data = self._get_consensus_data()
    self._do_validate(consensus_data)

  def attest(self, epoch: int):
    print("attest")
    """Get rewards data from another validator and attest that data if valid"""
    validator_consensus_data = self._get_validator_consensus_data(epoch)
    print("validator_consensus_data1", validator_consensus_data)
    print("validator_consensus_data type", type(validator_consensus_data))
    print("validator_consensus_data[data] type", type(validator_consensus_data["data"]))

    if validator_consensus_data == None:
      logger.info("Waiting for validator to submit")
      return None

    validator_consensus_data = RewardsData.list_from_scale_info(validator_consensus_data["data"])
    print("validator_consensus_data2", validator_consensus_data)

    valid = True

    logger.info("Checking if we should attest the validators submission")


    """
    """
    # Simply validate to ensure mechanism compatibility

    logger.info("Generating consensus data")
    consensus_data = self._get_consensus_data()
    should_attest = self.should_attest(validator_consensus_data, consensus_data)
    logger.info("Should attest is: %s", should_attest)

    if valid:
      logger.info("Validators data is confirmed valid, attesting data...")
      return self._do_attest()
    else:
      logger.info("Validators data is not valid, skipping attestation.")
      return None
    
  def _do_validate(self, data):
    print("_do_validate")
    try:
      receipt = validate(
        self.substrate_config.interface,
        self.substrate_config.keypair,
        self.subnet_id,
        data
      )
      return receipt
    except Exception as e:
      logger.error("Validation Error: %s" % e)
      return None
    
  def _do_attest(self):
    print("_do_attest")
    try:
      receipt = attest(
        self.substrate_config.interface,
        self.substrate_config.keypair,
        self.subnet_id,
      )
      return receipt
    except Exception as e:
      logger.error("Attestation Error: %s" % e)
      return None

  def _get_consensus_data(self):
    print("_get_consensus_data")
    """"""
    consensus_data = get_subnet_nodes_consensus_data(PEERS_LENGTH)
    print("consensus_data", consensus_data)
    return consensus_data

  def _get_validator_consensus_data(self, epoch: int):
    print("_get_validator_consensus_data")
    """Get and return the consensus data from the current validator"""
    rewards_submission = get_rewards_submission(
      self.substrate_config.interface,
      self.subnet_id,
      epoch
    )
    return rewards_submission

  def _get_validator(self, epoch):
    print("_get_validator")
    validator = get_rewards_validator(
      self.substrate_config.interface,
      self.subnet_id,
      epoch
    )
    return validator
  
  def _activate_subnet(self):
    print("_activate_subnet")
    """
    Attempt to activate subnet

    Will wait for subnet to be voted in

    Returns:
      bool: True if subnet was successfully activated, False otherwise.
    """
    activated = get_model_activated(self.substrate_config.interface, self.path)

    if activated['active'] == True:
      logger.info("Subnet activated, just getting things set up for consensus...")
      self.subnet_accepting_consensus = True

      subnet_id = get_model_path_id(
        self.substrate_config.interface,
        self.path
      )

      self.subnet_id = int(str(subnet_id))

      subnet_data = get_model_data(
        self.substrate_config.interface,
        subnet_id
      )

      self.subnet_initialized = int(str(subnet_data["initialized"]))

      return True
    else:
      return False

  def should_attest(self, validator_data, my_data):
    """Checks if two arrays of dictionaries match, regardless of order."""

    if len(validator_data) != len(my_data) or len(validator_data) > 0:
      return False

    # use ``asdict`` because data is decoded from blockchain as dataclass
    if is_dataclass(validator_data[0]):
      set1 = set(frozenset(asdict(d).items()) for d in validator_data)
    else:
      set1 = set(frozenset(d.items()) for d in validator_data)

    if is_dataclass(my_data[0]):
      set2 = set(frozenset(asdict(d).items()) for d in my_data)
    else:
      set2 = set(frozenset(d.items()) for d in my_data)

    intersection = set1.intersection(set2)
    logger.info("Matching intersection of %s validator data" % ((len(intersection))/len(set1) * 100))
    logger.info("Validator matching intersection of %s my data" % ((len(intersection))/len(set2) * 100))

    return set1 == set2

def run_consensus(num: int):
    print("run_consensus")
    if asyncio.get_event_loop().is_running():
        asyncio.get_event_loop().stop()  # if we're in jupyter, get rid of its built-in event loop
        asyncio.set_event_loop(asyncio.new_event_loop())

    loop = asyncio.get_event_loop()
    print("loop")

    substrate_config = get_substrate_config(num)
    print("substrate_config")
    consensus = TestConsensus(
      path=MODEL_PATH, 
      account_id=substrate_config.account_id, 
      phrase=f"//{str(num)}", 
      url=LOCAL_URL
    )

    loop.run_until_complete(consensus.run())

    async def shutdown():
      print("Stopping the event loop...")
      loop.stop()

    loop.add_signal_handler(signal.SIGTERM, lambda: loop.create_task(shutdown()))
    loop.run_forever()

def test_validate(count: int):
  print("test_validate get_substrate_config")
  substrate_config = get_substrate_config(0)
  print("test_validate get_model_path_id")
  subnet_id = get_model_path_id(
    substrate_config.interface,
    MODEL_PATH
  )

  print("test_validate subnet_id", subnet_id)

  assert subnet_id != 0, "Subnet not initialized with ID: 1"

  # add subnet nodes
  print("test_validate adding subnet nodes")
  test_add_subnet_nodes(count)

  processes = []
  for n in range(count):
    proc = mp.Process(target=run_consensus, args=(n,), daemon=False)
    proc.start()
    processes.append(proc)

if __name__ == "__main__":
  print("Starting test validate")
  test_validate(PEERS_LENGTH)