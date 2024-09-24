from dataclasses import asdict, is_dataclass
import threading
import time
from petals_tensor.substrate.chain_data import RewardsData
from petals_tensor.substrate.chain_functions import attest, get_epoch_length, get_min_required_model_consensus_submit_epochs, get_model_activated, get_model_data, get_model_path_id, get_rewards_submission, get_rewards_validator, validate
from petals_tensor.substrate.config import BLOCK_SECS, SubstrateConfig
from petals_tensor.substrate.utils import get_consensus_data, get_eligible_consensus_block, get_next_epoch_start_block, get_submittable_nodes
# from petals_tensor.validator.inference_validator import InferenceValidator
from hivemind.utils import get_logger

logger = get_logger(__name__)

class Consensus(threading.Thread):
  """
  Houses logic for validating and attesting consensus data per epochs for rewards

  This can be ran before or during a model activation.

  If before, it will wait until the subnet is successfully voted in, if the proposal to initialize the subnet fails,
  it will not stop running.

  If after, it will begin to validate and or attest epochs
  """
  def __init__(self, path: str, account_id: str):
    super().__init__()
    self.subnet_id = None # Not required in case of not initialized yet
    self.path = path
    self.account_id = account_id
    self.subnet_accepting_consensus = False
    self.subnet_node_eligible = False
    self.subnet_initialized = 9223372036854775807 # max int
    self.last_validated_or_attested_epoch = 0

    # blockchain constants
    self.epoch_length = int(str(get_epoch_length(SubstrateConfig.interface)))
    self.min_required_model_consensus_submit_epochs = get_min_required_model_consensus_submit_epochs(SubstrateConfig.interface)

    # delete pickles if exist

    # create clean pickles

  def run(self):
    while True:
      """"""
      try:
        # get epoch
        block_hash = SubstrateConfig.interface.get_block_hash()
        block_number = SubstrateConfig.interface.get_block_number(block_hash)
        logger.info("Block height: %s " % block_number)

        epoch = int(block_number / self.epoch_length)
        logger.info("Epoch: %s " % epoch)

        next_epoch_start_block = get_next_epoch_start_block(
          self.epoch_length, 
          block_number
        )
        remaining_blocks_until_next_epoch = next_epoch_start_block - block_number
        
        # skip if already validated or attested epoch
        if epoch <= self.last_validated_or_attested_epoch:
          logger.info("Already completed epoch: %s, waiting for the next " % epoch)
          time.sleep(remaining_blocks_until_next_epoch * BLOCK_SECS)
          continue

        # Ensure subnet is activated
        if self.subnet_accepting_consensus == False:
          activated = self._activate_subnet()
          if activated == True:
            continue
          else:
            # Sleep until voting is complete
            time.sleep(remaining_blocks_until_next_epoch * BLOCK_SECS)
            continue

        # The subnet is activated at this point
        # 1. Check if subnet can accept consensus
        # 2. Check if node is submittable
        """
        Is subnet initialized
        """
        subnet_eligible_block = get_eligible_consensus_block(
          self.epoch_length, 
          self.subnet_initialized, 
          self.min_required_model_consensus_submit_epochs
        )

        is_subnet_consensus_eligible = subnet_eligible_block != None and block_number >= subnet_eligible_block

        # is subnet eligible for consensus (must be initialized for minimum required epochs)
        if is_subnet_consensus_eligible == False:
          delta = subnet_eligible_block - block_number
          logger.info("Model begins accepting consensus on block %s, going to sleep for %s blocks " % (subnet_eligible_block, delta))
          time.sleep(delta * BLOCK_SECS)
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
          submittable_nodes = get_submittable_nodes(SubstrateConfig.interface, self.subnet_id)

          for node_set in submittable_nodes:
            if node_set[0] == self.account_id:
              self.subnet_node_eligible = True
              break
          
          if self.subnet_node_eligible == False:
            logger.info("Node not eligible for consensus, sleeping until next epoch")
            time.sleep(remaining_blocks_until_next_epoch * BLOCK_SECS)
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
          time.sleep(remaining_blocks_until_next_epoch * BLOCK_SECS)
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
          block_hash = SubstrateConfig.interface.get_block_hash()
          block_number = SubstrateConfig.interface.get_block_number(block_hash)
          logger.info("Block height: %s " % block_number)

          epoch = int(block_number / self.epoch_length)
          logger.info("Epoch: %s " % epoch)

          # If we made it to the next epoch, break
          # This likely means the chosen validator never submitted consensus data
          if epoch > initial_epoch:
            logger.info("Validator didn't submit consensus data, moving to the next epoch: %s" % epoch)
            break

          result = self.attest(epoch)
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
    """Get rewards data and submit consensus"""
    consensus_data = self._get_consensus_data()
    self._do_validate(consensus_data["peers"])

  def attest(self, epoch: int):
    """Get rewards data from another validator and attest that data if valid"""
    validator_consensus_submission = self._get_validator_consensus_submission(epoch)

    if validator_consensus_submission == None:
      logger.info("Waiting for validator to submit")
      return None

    # backup check if validator node restarts in the middle of an epoch to ensure they don't tx again
    if self._has_attested(validator_consensus_submission["attests"]):
      logger.info("Has attested already")
      return None
    
    validator_consensus_data = RewardsData.list_from_scale_info(validator_consensus_submission["data"])
    
    logger.info("Checking if we should attest the validators submission")
    logger.info("Generating consensus data")
    consensus_data = self._get_consensus_data()
    should_attest = self.should_attest(validator_consensus_data, consensus_data)
    logger.info("Should attest is: %s", should_attest)

    if should_attest:
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
    """"""
    consensus_data = get_consensus_data(SubstrateConfig.interface, self.subnet_id)
    return consensus_data

  def _get_validator_consensus_submission(self, epoch: int):
    """Get and return the consensus data from the current validator"""
    rewards_submission = get_rewards_submission(
      SubstrateConfig.interface,
      self.subnet_id,
      epoch
    )
    return rewards_submission

  def _has_attested(self, attested_account_ids) -> bool:
    """Get and return the consensus data from the current validator"""
    for account_id in attested_account_ids:
      if account_id == self.account_id:
        return True
    return False

  def _get_validator(self, epoch):
    validator = get_rewards_validator(
      SubstrateConfig.interface,
      self.subnet_id,
      epoch
    )
    return validator
  
  def _activate_subnet(self):
    """
    Attempt to activate subnet

    Will wait for subnet to be voted in

    Returns:
      bool: True if subnet was successfully activated, False otherwise.
    """
    activated = get_model_activated(SubstrateConfig.interface, self.path)

    if activated['active'] == True:
      logger.info("Subnet activated, just getting things set up for consensus...")
      self.subnet_accepting_consensus = True
      subnet_id = get_model_path_id(
        SubstrateConfig.interface,
        self.path
      )
      self.subnet_id = int(str(subnet_id))

      subnet_data = get_model_data(
        SubstrateConfig.interface,
        subnet_id
      )

      self.subnet_initialized = int(str(subnet_data["initialized"]))

      return True
    else:
      return False

  def should_attest(self, validator_data, my_data):
    """Checks if two arrays of dictionaries match, regardless of order."""

    if len(validator_data) != len(my_data) and len(validator_data) > 0:
      return False

    # use ``asdict`` because data is decoded from blockchain as dataclass
    if is_dataclass(validator_data):
      set1 = set(frozenset(asdict(d).items()) for d in validator_data)
    else:
      set1 = set(frozenset(d.items()) for d in validator_data)

    if is_dataclass(my_data):
      set2 = set(frozenset(asdict(d).items()) for d in my_data)
    else:
      set2 = set(frozenset(d.items()) for d in my_data)

    intersection = set1.intersection(set2)
    logger.info("Matching intersection of %s validator data" % ((len(intersection))/len(set1) * 100))
    logger.info("Validator matching intersection of %s my data" % ((len(intersection))/len(set2) * 100))

    return set1 == set2
