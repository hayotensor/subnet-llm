import itertools
import random
import threading
from typing import Dict, List, Optional
import dataclasses
import time
import multiprocessing as mp

from petals_tensor.validator.routing.sequence_manager import MissingBlocksError
from petals_tensor.data_structures import RemoteSpanInfo
from petals_tensor.health.state_updater import get_peers_data_list
# from petals_tensor.server.server_test_2 import Server
# from petals_tensor.substrate.chain_functions import propose_model_peer_dishonest, vote_model_peer_dishonest
from petals_tensor.utils.auto_config import AutoDistributedModelForCausalLMValidator
# from petals_tensor.substrate import config as substrate_config

from transformers import AutoTokenizer
import numpy as np
import torch
from hivemind.utils.logging import get_logger
from hivemind import PeerID

logger = get_logger(__name__)

# TODO: make substrate_config a class
# from hypertensor import HypertensorClient

"""Timespan per inference validation per peer"""
TIMESPAN_PER_PEER = 300
STRICT_BLOCK_WAIT = 300
MODULE_CONTAINER_WAIT = 90

"""Inference configuration"""

# DO NOT change these. All accountants must have the same inference validation configuration
RTOL = 1e-03
ATOL = 8e-01

class InferenceValidator(threading.Thread):
    """
    Runs Inference validation logic, runs per epoch,
    called by Server to start processing before ModuleContainers load, 
    inherits Server reference to update variables in the Server,
    and updates the server's state.
    """

    def __init__(self, server, model_name, num_model_blocks: int, my_peer_id: str, num_blocks: int, start: bool):
        super().__init__()
        print("__init__ InferenceValidator")
        print("__init__ InferenceValidator model_blocks", num_model_blocks)
        print("__init__ InferenceValidator num_blocks", num_blocks)
        self.server = server
        self.model = AutoDistributedModelForCausalLMValidator.from_pretrained(model_name)
        # self.client = substrate_config.SubstrateConfig
        self.peers_data = None
        self.validated_peer_ids = None
        self.last_timestamp = 0
        self.my_peer_id = my_peer_id
        self.model_name = model_name
        self.num_blocks = num_blocks
        self.ranges = list(itertools.combinations(range(0,num_model_blocks+1), 2))

        self.accountant_data = AccountantData()

        # TODO: Get blacklisted peers to automatically create a dishonesty proposal for them


        # epoch represents the time it takes to validate each peer they are designated to validdate inference
        # TODO: Make universal for each accountant to pull from the same storage backend
        self.epoch = 0

        # tokenizer = AutoTokenizer.from_pretrained(model_name)
        # self.input_data = tokenizer("A cat sat", return_tensors="pt")["input_ids"]
        self.input_data = "A cat sat"
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        
        if start:
            self.run_in_background(await_ready=True)

        self.stop = threading.Event()

    # def run2(self):
    #     print("run InferenceValidator")
    #     while True:

    #         if self.server.module_container is None:
    #             time.sleep(30)
    #             logger.info(f"module_container is None, sleeping for {MODULE_CONTAINER_WAIT}s")
    #             continue

    #         block_span_initialized = self.server.module_container.is_healthy()
    #         print("run block_span_initialized", block_span_initialized)
    #         if block_span_initialized == False:
    #             # sleep example
    #             logger.info(f"block_span_initialized == False, sleeping for {MODULE_CONTAINER_WAIT}s")
    #             time.sleep(30)
    #             continue

    #         """
    #         Check current epoch, ensure epoch not already validated

    #         Each validator will check each peer or designated peers at random per epoch
            
    #         We ensure here that the validator is not processing duplicate inference validations
    #         on the same epoch. Sleep until the next epoch is reached
            
    #         """
    #         epoch = self._get_epoch()
    #         print("run epoch", epoch)
    #         print("run self.epoch", self.epoch)

    #         if epoch == self.epoch:
    #             # do calculations to set to wait remaining time period
    #             print("epoch == self.epoch")

    #             # sleep example
    #             time.sleep(30)
    #             continue
        
    #         # self.epoch = epoch

    #         """Set as validator for full control over blocks"""
    #         self.server.is_validator = True

    #         try:
    #             """Get all peers and update list of peers for this epoch"""
    #             logger.info("Updating peers within the epoch")
    #             self.update_peers()

    #             logger.info("Retrieve PeerIDs to validate")
    #             # Get all peer_ids to validate 
    #             peer_ids = self.select_peer_ids()
    #             # print("iv peer_ids", peer_ids)

    #             # Manual peers for testing purposes
    #             # peer_ids = ["12D3KooWCWrjfLzYL4zJevm35MzexAwjngE1WNPYeiHrsLTGEkoy"]

    #             for peer_id in peer_ids:
    #                 # Get peer data for inference validation
    #                 logger.info("Getting peer data")
    #                 peer_data = self._get_peer_data(peer_id)

    #                 # Get block spans
    #                 span_start = peer_data['span_start']
    #                 span_end = peer_data['span_end']
    #                 block_indices = f"{span_start}:{span_end}"
                    
    #                 #  Spans must be in order based on using custom sequence logic
    #                 if span_end - span_start > self.num_blocks:
    #                     # TODO: Do not go over max blocks but shouldn't be possible here
    #                     span_end = span_start + self.num_blocks

    #                 logger.info(f"Validating inference on {peer_id} with indices {block_indices}")

    #                 peers = [{
    #                     'peer_id':peer_id,
    #                     'start':span_start,
    #                     'end':span_end,
    #                 }]

    #                 # TODO: If a server has a larger block span than the validator, we must only check
    #                 #       the block span up to the validators block span.
    #                 #       This is done by including peers block spans parameter in the _make_sequence_with_specific_peers
    #                 #       function within the sequence_manager.py file within the routing directory.

    #                 # Update validator block spans
    #                 logger.info(f"Updating validator blocks to {block_indices} if needed".format())
    #                 self.server.update_strict_block_indices(block_indices)

    #                 # Begin inference validation
    #                 while True:
    #                     logger.info(f"Verifying validator blocks have begun")
    #                     # Wait for new blocks to be healthy
    #                     if self.server.module_container is None:
    #                         logger.info("Module container not loaded yet")
    #                         time.sleep(MODULE_CONTAINER_WAIT)
    #                         continue

    #                     # Example: Wait until blocks are updated
    #                     # This is a hack attempt - need to instead check for that blocks have been updated to the correct spans
    #                     logger.info(f"Verifying validator blocks are healthy")
    #                     new_block_span_updated = self.server.module_container.is_healthy()

    #                     if new_block_span_updated == False:
    #                         logger.info("Module container not healthy yet")
    #                         time.sleep(MODULE_CONTAINER_WAIT)
    #                         continue

    #                     # Run inference on expected block spans to get expected output
    #                     output = self.run_inference(self.input_data, peer_id=self.my_peer_id)
    #                     print("validator output", output)

    #                     # Run inference on peer and check if output is expected
    #                     valid = self.validate_inference(self.input_data, output, peers=[peers])
    #                     print("iv valid", valid)

    #                     # If not valid, propose or vote dishonesty
    #                     if valid == False:
    #                         self.initiate_dishonesty(peer_id)
                        
    #                     break
    #         except Exception as e:
    #             print("Error", e)
    #         finally:
    #             self.server.remove_strict_block_indices()
    #             self.server.is_validator = False

    def run(self):
        while True:
            # Begin epoch 
            epoch = self._get_epoch()

            # Reset accountant data for the new epoch
            self.accountant_data.reset()

            # Ensure that module container is created
            logger.info(f"Verifying validator blocks have begun")
            if self.server.module_container is None:
                # Restart loop
                logger.info("Module container not loaded yet")
                time.sleep(30)
                continue

            # Ensure that module container is initialized
            logger.info(f"Verifying validator blocks are healthy")
            if not self.server.module_container.is_healthy():
                # Restart loop
                logger.info("Module container not healthy yet")
                time.sleep(30)
                continue

            try:
                """Set as validator for full control over blocks"""
                # In prod, ensure peer CAN be an accountant by checking blockchain status
                logger.info("Setting status to validator")
                self.server.is_validator = True

                while True:
                    # Updating peers per epoch
                    # We block updating peers if already updated here
                    # In prod this will check timestamps and epochs
                    if self.epoch == 0 and self.peers_data is None:
                        # Restart loop
                        logger.info("Updating peers within the epoch")
                        self.update_peers()
                    
                    if self.peers_data is None:
                        # Restart loop
                        logger.info("peers_data is None, sleeping for 30s and trying again")
                        time.sleep(30)
                        continue

                    """Get each peer matchig the range of blocks"""
                    logger.info("Retrieving peers to validate")
                    for range in self.ranges:
                        span_start = range[0]
                        span_end = range[1]

                        # Get peers data in this range
                        peers_data = self._get_peers_data_in_range(span_start, span_end)
                        peers_len = len(peers_data)

                        if peers_len == 0:
                            # Move onto next range
                            continue

                        print(span_start, span_end)

                        logger.info(f"Found {peers_len} peers to validate with span {span_start}:{span_end}")

                        # Update up to validators max blocks
                        if span_end - span_start > self.num_blocks:
                            logger.info(f"Peer blocks are greater than validator blocks of {self.num_blocks}, updating sequence to match max validator span")
                            span_end = span_start + self.num_blocks
                            # span_start = span_end - self.num_blocks

                        block_indices = f"{span_start}:{span_end}"

                        # Update validator block spans
                        logger.info(f"Updating validator blocks to {block_indices} if needed".format())
                        self.server.update_strict_block_indices(block_indices)

                        while True:
                            # Do something pythonic than just sleep
                            time.sleep(30)

                            # Wait for new blocks to be healthy
                            logger.info(f"Verifying validator blocks have begun")
                            if self.server.module_container is None:
                                # Restart loop
                                logger.info("Module container not loaded yet")
                                time.sleep(MODULE_CONTAINER_WAIT)
                                continue

                            # start_block = self.server.server_info.start_block
                            # end_block = self.server.server_info.end_block
                            start_block = self.server.module_container.server_info.start_block
                            end_block = self.server.module_container.server_info.end_block

                            print("validator current start block", start_block)
                            print("validator current end   block", end_block)

                            if start_block != span_start and end_block != span_end:
                                logger.info("Blocks don't match yet")
                                time.sleep(MODULE_CONTAINER_WAIT)
                                continue

                            # Example: Wait until blocks are updated
                            # This is a hack attempt - need to instead check for that blocks have been updated to the correct spans
                            logger.info(f"Verifying validator blocks are healthy")

                            if not self.server.module_container.is_healthy():
                                # Restart loop
                                logger.info("Module container not healthy yet")
                                time.sleep(MODULE_CONTAINER_WAIT)
                                continue

                            # Run inference on expected block spans to get expected output
                            logger.info(f"Running inference as validator for expected output")
                            output = self.run_inference(
                                self.input_data, 
                                span_start,
                                span_end,
                                peer_id=self.my_peer_id
                            )

                            """Do something here to reset"""
                            if output is None:
                                logger.error("Error running inference, retrying...")
                                # If there is an error, the peers that broke the inference sequence will be blacklisted
                                # Try again
                                time.sleep(12)
                                continue

                            logger.info(f"Successfully ran inference as validator for expected output")
                            break

                        # TODO: Get multiple peers in one generate() call instead of per peer
                        #       if able to validate inference between multiple peers at once
                        #       i.e. if validator has 18 blocks and 2 peers each have 9 blocks
                        #            equalling 18, validate in one generate() call

                        # Iterate each peer and validate inference
                        logger.info(f"Beginning inference validation on peers within range")
                        for peer_data in peers_data:
                            time.sleep(30)

                            peer_id = peer_data['peer_id']
                            logger.info(f"peer_data in peers_data {peer_id}")

                            # Add peer to be in sequence to check blocks spans
                            peers = [{
                                'peer_id':peer_id,
                                'start':span_start,
                                'end':span_end,
                            }]

                            # Run inference on peer and check if output is expected
                            logger.info(f"Validating inference on {peer_id} with indices {block_indices}")
                            valid = self.validate_inference(
                                self.input_data,
                                span_start,
                                span_end,
                                output, 
                                peer_id,
                                peers
                            )

                            # If not valid, propose or vote dishonesty
                            if valid == False:
                                logger.info(f"Inference is not valid for {peer_id}")
                                self.initiate_dishonesty(peer_id)
                            else:
                                logger.info(f"Inference is valid for {peer_id}")

                    # `break` once all peers are checked and wait until next epoch to start again
                    logger.info(f"Inference validation on epoch {self.epoch} completed successfully")
                    break
            except Exception as e:
                print("Error", e)
            finally:
                self.server.remove_strict_block_indices()
                self.server.is_validator = False
                seconds_remaining_in_epoch = self._get_seconds_remaining_in_epoch()
                logger.info(f"Next epoch is {seconds_remaining_in_epoch} seconds away, sleeping for remaining time")
                time.sleep(seconds_remaining_in_epoch)

    def run_in_background(self, await_ready=True, timeout=None):
        self.start()

    def shutdown(self):
        logger.info("Shutting down inference validator")
        self.stop.set()
        self.exit()

    def select_peer_ids(self):
        # Logic to select peers for validation
        peer_ids = [i['peer_id'] for i in self.peers_data]

        return [peer_ids[0]]  # Example: select the first peer

    def update_blocks(self, peer_blocks):
        # Logic to update validator's blocks to match peer's blocks
        self.blocks = peer_blocks

    def set_deterministic(self):
        torch.manual_seed(0)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False

    def run_inference(
        self, 
        input_data, 
        span_start,
        span_end,
        peers: Optional[List[Dict]] = None, 
        peer_id: Optional[str] = None
    ):
        """Run inference and return the results from the span_start to the span_end"""
        # TODO: Get inference only up to the end block required to run inference checks to save on compute
        _input_data = self.tokenizer(input_data, return_tensors="pt")["input_ids"]

        if peers is not None:
            inference_session_data, outputs = self.model.generate(
                _input_data, 
                peers=peers,
                max_new_tokens=5
            )
        elif peer_id is not None:
            inference_session_data, outputs = self.model.generate(
                _input_data, 
                peer_id=peer_id, 
                max_new_tokens=5
            )
            inference_data = self.get_peer_inference_results(inference_session_data, peer_id, span_start, span_end)

        print("run_inference results", outputs)
        print("run_inference inference_data", inference_data["outputs"])

        return inference_data["outputs"] if inference_data is not None else None
        # return outputs

    def validate_inference(
        self, 
        input_data, 
        span_start, 
        span_end, 
        expected_outputs, 
        peer_id: str,
        peers: List[Dict]
    ) -> bool:
        """Validate the inference from the span_start - span_end"""
        try:
            _input_data = self.tokenizer(input_data, return_tensors="pt")["input_ids"]

            # Perform inference
            inference_session_data, outputs = self.model.generate(
                _input_data, 
                peers=peers, 
                max_new_tokens=5
            )

            inference_data = self.get_peer_inference_results(inference_session_data, peer_id, span_start, span_end)
            print("validate_inference inference_data", inference_data)

            # TODO: Add edge checking
            if inference_data is not None:
                outputs = inference_data["outputs"]
                expected_outputs_tensor_sum = torch.sum(expected_outputs)
                outputs_tensor_sum = torch.sum(outputs)

                tensor_diff = expected_outputs_tensor_sum - outputs_tensor_sum
                tensor_sum = outputs_tensor_sum

                logger.info(f"Tensor sum diff is:              {tensor_diff}/{-tensor_diff}")
                logger.info(f"Max tensor sum diff is:          {-ATOL}/{ATOL}")
                logger.info(f"Expected output tensor sum is:   {expected_outputs_tensor_sum}")
                logger.info(f"Validating output tensor sum is: {outputs_tensor_sum}")


                valid = torch.allclose(expected_outputs, outputs, rtol=RTOL, atol=ATOL, equal_nan=False)

                peer_inference_data = PeerInferenceSequenceData(
                    peer_id=peer_id,
                    span_start=span_start,
                    span_end=span_end,
                    accountant_tensor_sum=expected_outputs_tensor_sum,
                    tensor_sum=tensor_sum,
                    valid=valid,
                )
                self.accountant_data.add_data(peer_inference_data)

                # rtol: relative tolerance at 1e-03 or as float 0.001
                # atol: absolute tolerance at 8e-01 or as float 0.8
                # equal_nan: True to compare NaN's as equal, False otherwise.
                # If both are False, then NaN's will be treated as unequal.
                # If both are True, then NaN's will be treated as equal.
                # If both are None, then the behavior is equivalent to False.
                return valid
            else:
                peer_inference_data = PeerInferenceSequenceData(
                    peer_id=peer_id,
                    span_start=span_start,
                    span_end=span_end,
                    accountant_tensor_sum=0,
                    tensor_sum=0,
                    valid=False,
                )
                self.accountant_data.add_data(peer_inference_data)

                return False
        except Exception as e:
            logger.warning(f"Inference Validation Error: {e}")
            return False

        # Check if the output matches the expected output
        # TODO: Add edge checking
        # return torch.equal(outputs, expected_outputs)

    def get_peer_inference_results(self, inference_session_data, peer_id, span_start, span_end):
        inference_data = None
        for data in inference_session_data:
            if data['peer_id'].__eq__(peer_id) and data['span_start'] == span_start and data['span_end'] == span_end:
                inference_data = data
                break
        return inference_data

    def initiate_dishonesty(self, peer_id):
        # Propose the peer as dishonest on the blockchain

        # Check if proposal already exists
        proposal_exists = True 
        # if proposal_exists:
        #     tx_hash = vote_model_peer_dishonest(
        #         self.client.substrate_interface,
        #         self.client.keypair,
        #         model_id=0,  # Example: model id
        #         peer_id=peer_id,  # Example: peer id to vote as dishonest
        #     )
        # else:
        #     tx_hash = propose_model_peer_dishonest(
        #         self.client.substrate_interface,
        #         self.client.keypair,
        #         model_id=0,  # Example: model id
        #         peer_id=peer_id,  # Example: peer id to vote as dishonest
        #     )

        tx_hash = 0

        print(f"Proposed dishonest peer {peer_id} with transaction hash: {tx_hash}")

    def submit_data(self, data: str):
        """
        Submit data to the blockchain if chosen accountant on the epoch.

        The data must be formatted as a string to send to the blockchain
        This data can then be used for other subnet nodes to pull from the blockchain storage
        """
        # tx_hash = submit_data(
        #     self.client.substrate_interface,
        #     self.client.keypair,
        #     data=data,
        # )


    def recalibrate_blocks(self):
        # Logic to recalibrate blocks to the most optimized state
        print("Recalibrating blocks to the most optimized state")

    def update_peers(self):
        self.peers_data = []
        peers_data_list = get_peers_data_list()
        for peer in peers_data_list:
            if peer['peer_id'] != self.my_peer_id:  # Exclude the validator from the peer list
                self.peers_data.append(peer)

    def _get_peer_data(self, peer_id):
        return next((x for x in self.peers_data if x['peer_id'] == peer_id), None)

    def _get_peers_data_in_range(self, span_start: int, span_end: int) -> List:
        peers_data: List = []
        for peer in self.peers_data:
            if peer['peer_id'] != self.my_peer_id and peer['span_start'] == span_start and peer['span_end'] == span_end:  # Exclude the validator from the peer list
                peers_data.append(peer)

        return peers_data 
    
    def _is_accountant(self) -> bool:
        """Check blockchain if self is the chosen accountant to submit inference validataion data for the epoch"""
        return False

    def _get_epoch(self):
        """Do math to get epoch number from blockchain"""
        return 1

    def _get_seconds_remaining_in_epoch(self) -> int:
        """
        Get how much time is left in the epoch until the next epoch
        
        This is used to wait until the next epoch to begin inference validation again
        """
        return 100


@dataclasses.dataclass
class PeerInferenceSequenceData:
    """Class for storing node inferece and sequence data."""
    peer_id: PeerID # Peer ID of node accountant is checking inference of
    span_start: int # Start span of node accountant is checking inference of
    span_end: int # End span of node accountant is checking inference of
    accountant_tensor_sum: float # Tensor sum of the accountant performing inference validation
    tensor_sum: float # Tensor sum of the node accountant is checking inference of
    valid: bool # If accountant deems node checking inference of is valid

class AccountantData:
    def __init__(self):
        self.data = []

    def add_data(self, data: PeerInferenceSequenceData):
        self.data.append(data)

    def reset(self):
        self.data = []