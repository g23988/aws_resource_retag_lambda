# -*- coding: utf-8 -*-
#
# Retag_agent - Get EC2's special tags and automatically tag ENI and EBS
#
# Author: Wei Liu
#
# Change History
#  2020-09-01: Wei Liu,   1)Initial release
#  2024-07-23: Wei Liu,   1)Update chunk() to splitIntoChunks()
#  2024-07-29: Wei Liu,   1)Refactoring greatly improves efficiency
#

import json
import boto3
import os
from botocore.config import Config
from concurrent.futures import ThreadPoolExecutor, as_completed

# setup the logging service as logger
import logging as lg
logger = lg.getLogger()
logger.setLevel(lg.INFO)

#========================
# Helper Functions
#========================
def split_into_chunks(arr, chunksize):
    """ Split a list into smaller lists of a specified size """
    return [arr[i:i + chunksize] for i in range(0, len(arr), chunksize)]
    
def array_to_dict_with_empty_string(array):
    """ Convert an array to a dictionary with a default value for each key """
    return {key: "" for key in array}

#========================
# Configuration
#========================
max_workers = 256
target_tags = array_to_dict_with_empty_string(json.loads(os.environ['TargetTags']))
#logger.info("Following tags will put into EBS and ENI. {target_tags}".format(target_tags=target_tags))

#========================
# Initialization
#========================
config = Config(
    retries={
        'max_attempts': 10,
        'mode': 'standard'
    },
    max_pool_connections=max_workers
)
ec2 = boto3.client('ec2',config=config)


#========================
# Fuctions
#
# verbose == True will turn on debug mode
#========================

def fetch_all_instances(verbose = False):
    """ fetch all of the ec2 instances information. """
    response = ec2.describe_instances()
    if verbose:
        logger.info(f"Get all instances from ec2 API. {response}")
    instances = {
        instance['InstanceId']:{
            'VolumeIds': [bdm['Ebs']['VolumeId'] for bdm in instance.get('BlockDeviceMappings', []) if 'Ebs' in bdm],
            'NetworkInterfaceIds': [ni['NetworkInterfaceId'] for ni in instance.get('NetworkInterfaces', []) if 'NetworkInterfaceId' in ni]
        }
        for reservation in response['Reservations']
        for instance in reservation['Instances']
    }
    if verbose:
        logger.info(f"Ec2 information. {instances}")
    return instances
    
def extract_instance_ids(instances, verbose = False):
    """ Extract instance IDs from instance information  """
    if verbose:
        logger.info(f"Extracting instance IDs from {len(instances)} instances")
    return list(instances.keys())

def fetch_instance_tags(instance_ids, verbose = False):
    """ fetch tags for a list of instances IDs. """
    tags = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(ec2.describe_tags, Filters=[{'Name': 'resource-id', 'Values': chunk}]) for chunk in split_into_chunks(instance_ids, 200)]
        for future in as_completed(futures):
            response = future.result()
            tags.extend(response['Tags'])
    if verbose:
        logger.info(f"Fetched all instances' tags: {tags}")
    return tags

def merge_tags_into_instances(instances, tags, verbose = False):
    """ Merge target tags into the instances' Dict """
    for tag in tags:
        instance_id = tag['ResourceId']
        if instance_id in instances and tag['Key'] in target_tags:
            instances[instance_id].setdefault('Tags', []).append({'Key': tag['Key'], 'Value': tag['Value']})
    if verbose:
        logger.info(f"Merged tags into instances.{instances}")
    return instances

def create_tags_for_resource(resource_ids, tags):
    """ Create tags for the given resource IDs """
    ec2.create_tags(Resources=resource_ids, Tags=tags)

def create_tags_for_ebs_eni(instances, verbose = False):
    """ create the same tags into ebs and eni from the instance """
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for instance_id, instance in instances.items():
            resource_ids = instance['VolumeIds'] + instance['NetworkInterfaceIds']
            tags = instance.get('Tags', [])
            futures.append(executor.submit(create_tags_for_resource, resource_ids, tags))
            if verbose:
                logger.info(f"Added tags {tags} to resources {resource_ids}")
        # Wait for all futures to complete
        for future in as_completed(futures):
            future.result()


#========================
# Main Function
#========================
def lambda_handler(event, context):
    all_instances = fetch_all_instances()
    instance_ids = extract_instance_ids(all_instances)
    all_tags = fetch_instance_tags(instance_ids)
    instances_with_tags = merge_tags_into_instances(all_instances, all_tags)
    create_tags_for_ebs_eni(instances_with_tags)

    return {
        'statusCode': 200,
        'body': json.dumps('Looks good!')
    }



