#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Core functions of TV."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging

import os
import numpy as np
import tensorflow as tf
slim = tf.contrib.slim
import incl.tensorvision.utils as utils

flags = tf.app.flags
FLAGS = flags.FLAGS

tf.app.flags.DEFINE_boolean(
    'summary', True, ('Whether or not to save summaries to tensorboard.'))

def _get_init_fn(exclude_scopes=None,checkpoint_path=None,):
  """Returns a function run by the chief worker to warm-start the training.

  Note that the init_fn is only run when initializing the model during the very
  first global step.

  Returns:
    An init function run by the supervisor.
  """
  if checkpoint_path is None:
    return None

  # Warn the user if a checkpoint exists in the train_dir. Then we'll be
  # ignoring the checkpoint anyway.


  exclusions = []
  if exclude_scopes:
    exclusions = [scope.strip()
                  for scope in FLAGS.checkpoint_exclude_scopes.split(',')]

  # TODO(sguada) variables.filter_variables()
  variables_to_restore = []
  for var in slim.get_model_variables():
    excluded = False
    for exclusion in exclusions:
      if var.op.name.startswith(exclusion):
        excluded = True
        break
    if not excluded:
      variables_to_restore.append(var)

  if tf.gfile.IsDirectory(FLAGS.checkpoint_path):
    checkpoint_path = tf.train.latest_checkpoint(FLAGS.checkpoint_path)
  else:
    checkpoint_path = FLAGS.checkpoint_path

  tf.logging.info('Fine-tuning from %s' % checkpoint_path)

  return slim.assign_from_checkpoint_fn(checkpoint_path, variables_to_restore, ignore_missing_vars=True)

def _get_variables_to_train(trainable_scopes=None):
  """Returns a list of variables to train.

  Returns:
    A list of variables to train by the optimizer.
  """
  if trainable_scopes is None:
    return tf.trainable_variables()
  else:
    scopes = [scope.strip() for scope in trainable_scopes.split(',')]

  variables_to_train = []
  for scope in scopes:
    variables = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope)
    variables_to_train.extend(variables)
  return variables_to_train

def load_weights(checkpoint_dir, sess, saver):
    """
    Load the weights of a model stored in saver.

    Parameters
    ----------
    checkpoint_dir : str
        The directory of checkpoints.
    sess : tf.Session
        A Session to use to restore the parameters.
    saver : tf.train.Saver

    Returns
    -----------
    int
        training step of checkpoint
    """
    ckpt = tf.train.get_checkpoint_state(checkpoint_dir)
    if ckpt and ckpt.model_checkpoint_path:
        logging.info(ckpt.model_checkpoint_path)
        file = os.path.basename(ckpt.model_checkpoint_path)
        checkpoint_path = os.path.join(checkpoint_dir, file)
        saver.restore(sess, checkpoint_path)
        return int(file.split('-')[1])


def build_training_graph(hypes, queue, modules,trainable_scopes=None):
    """
    Build the tensorflow graph out of the model files.

    Parameters
    ----------
    hypes : dict
        Hyperparameters
    queue: tf.queue
        Data Queue
    modules : tuple
        The modules load in utils.

    Returns
    -------
    tuple
        (q, train_op, loss, eval_lists) where
        q is a dict with keys 'train' and 'val' which includes queues,
        train_op is a tensorflow op,
        loss is a float,
        eval_lists is a dict with keys 'train' and 'val'
    """

    data_input = modules['input']
    encoder = modules['arch']
    objective = modules['objective']
    optimizer = modules['solver']

    learning_rate = tf.placeholder(tf.float32)

    # Add Input Producers to the Graph
    with tf.name_scope("Inputs"):
        image, labels = data_input.inputs(hypes, queue, phase='train')
#logits obtain some input layers like pool5 pool7 and fcn_logits -----------------------------------------------yu
    # Run inference on the encoder network
    logits = encoder.inference(hypes, image, train=True)
#decoded_logits like logits ,but decoded_logits add a softmax to the output-----------------------------------------yu
    # Build decoder on top of the logits
    decoded_logits = objective.decoder(hypes, logits, train=True)

    # Add to the Graph the Ops for loss calculation.
    with tf.name_scope("Loss"):
        losses = objective.loss(hypes, decoded_logits,
                                labels)

    # Add to the Graph the Ops that calculate and apply gradients.
    with tf.name_scope("Optimizer"):
        global_step = tf.Variable(0, trainable=False)
        # Build training operation
        #train_op is what we want to train and loop ----------------------------------------------------------------yu
        variables_to_train = _get_variables_to_train(trainable_scopes=trainable_scopes)
        train_op = optimizer.training(hypes, losses,
                                      global_step, learning_rate,var_list=variables_to_train)

    with tf.name_scope("Evaluation"):
        # Add the Op to compare the logits to the labels during evaluation.
        eval_list = objective.evaluation(
            hypes, image, labels, decoded_logits, losses, global_step)

        summary_op = tf.summary.merge_all()

    graph = {}
    graph['losses'] = losses
    graph['eval_list'] = eval_list
    graph['summary_op'] = summary_op
    graph['train_op'] = train_op
    graph['global_step'] = global_step
    graph['learning_rate'] = learning_rate
    graph['decoded_logits'] = learning_rate

    return graph


def build_inference_graph(hypes, modules, image):
    """Run one evaluation against the full epoch of data.

    Parameters
    ----------
    hypes : dict
        Hyperparameters
    modules : tuble
        the modules load in utils
    image : placeholder

    return:
        graph_ops
    """

    with tf.name_scope("Validation"):

        logits = modules['arch'].inference(hypes, image, train=False)
#TODO decoded_logits equal to add_softmax(upscore32)
        decoded_logits = modules['objective'].decoder(hypes, logits,
                                                      train=False)
    return decoded_logits


def start_tv_session(hypes,exclude_scopes=None,checkpoint_path=None):
    """
    Run one evaluation against the full epoch of data.

    Parameters
    ----------
    hypes : dict
        Hyperparameters

    Returns
    -------
    tuple
        (sess, saver, summary_op, summary_writer, threads)
    """
    # Build the summary operation based on the TF collection of Summaries.
    if FLAGS.summary:
        tf.contrib.layers.summarize_collection(tf.GraphKeys.WEIGHTS)
        tf.contrib.layers.summarize_collection(tf.GraphKeys.BIASES)
        summary_op = tf.summary.merge_all()
    else:
        summary_op = None

    # Create a saver for writing training checkpoints.
    if 'keep_checkpoint_every_n_hours' in hypes['solver']:
        kc = hypes['solver']['keep_checkpoint_every_n_hours']
    else:
        kc = 10000.0

    saver = tf.train.Saver(max_to_keep=int(utils.cfg.max_to_keep))
    # config = tf.ConfigProto()
    # config.gpu_options.allow_growth=True
    sess = tf.get_default_session()

    # Run the Op to initialize the variables.
    if 'init_function' in hypes:
        _initalize_variables = hypes['init_function']
        _initalize_variables(hypes)
    else:
        init = tf.global_variables_initializer()
        sess.run(init)
    # TODO
    if hypes['restore_Inceptionv3']:
        init_fn=_get_init_fn(exclude_scopes=exclude_scopes,checkpoint_path=checkpoint_path)
        init_fn(sess)



    # Start the queue runners.
    coord = tf.train.Coordinator()
    threads = tf.train.start_queue_runners(sess=sess, coord=coord)

    # Instantiate a SummaryWriter to output summaries and the Graph.
    summary_writer = tf.summary.FileWriter(hypes['dirs']['output_dir'],
                                           graph=sess.graph)

    tv_session = {}
    tv_session['sess'] = sess
    tv_session['saver'] = saver
    tv_session['summary_op'] = summary_op
    tv_session['writer'] = summary_writer
    tv_session['coord'] = coord
    tv_session['threads'] = threads

    return tv_session
