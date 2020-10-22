"""
train.py

PURPOSE: This file defines the code for training the neural networks in pytorch
"""

from os import path
import models
import utils
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.tensorboard as tb


def cvae_loss_function(x_p, x, mu, log_var, alpha=0):
    """
    Loss for CVAE is BCE + KLD
        see Appendix B from Kingma and Welling 2014
    Need alpha for KL annealing
    """
    BCE = F.binary_cross_entropy(x_p, x, reduction="sum")
    KLD = -0.5 * torch.sum(1 + log_var - mu.pow(2) - log_var.exp())
    return BCE + alpha * KLD


def seed_random(rand_seed):
    torch.manual_seed(rand_seed)
    np.random.seed(rand_seed)


def init_device():
    if torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")
    return device


def init_logger(log_dir=None):
    train_logger, valid_logger = None, None
    if log_dir is not None:
        train_logger = tb.SummaryWriter(path.join(log_dir, "train"))
        valid_logger = tb.SummaryWriter(path.join(log_dir, "valid"))
    return train_logger, valid_logger


def train(args):
    """
    trains a model as specified by args
    """
    seed_random(args.rand_seed)
    device = init_device()
    train_log, valid_log = init_logger(log_dir=args.log_dir)

    # TODO: set up load_data functions - be best if return a data loader
    corpus = utils.Corpus(args.data)
    # This should return a dataloader or something to that effect
    train_data = utils.load_data(corpus.train, batch_size=args.batch_size)
    # This should return a dataloader or something to that effect
    valid_data = utils.load_data(corpus.valid, batch_size=args.batch_size)

    vocab = len(corpus.dictionary)
    model = models.CVAE(vocab, 300, 500, 100)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    if args.continue_training:
        model.load_model()
    model = model.to(device)

    # TODO: Change the ignore_index to padding index
    loss = nn.CrossEntropyLoss(ignore_index=0)

    global_step = 0
    for epoch in range(args.num_epoch):

        model.train()
        losses = []
        for x in train_data:
            break
            # Now we need to make sure everything in the batch has same size
            x = nn.utils.rnn.pad_sequence(x, padding_value=0).to(device)

            # TODO: Add teacher forcing - need to pass actual
            pred, mu, log_var = model(x, None)
            eos_tensor = torch.empty(1, x.shape[1]).to(device)
            eos_tensor.fill_(corpus.dictionary.word2idx["<EOS>"])
            gold = torch.cat([x, eos_tensor], dim=0)
            pred = pred.permute(1, 2, 0)
            gold = gold.permute(1, 0)
            loss_val = loss(pred, gold)

            optimizer.zero_grad()
            loss_val.backward()
            if args.grad_clip > 0.0:
                nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            optimizer.step()
            global_step += 1

            losses.append(loss_val.detach().cpu().numpy())
            if train_log is not None:
                train_log.add_scalar("loss", losses[-1], global_step)

        model.eval()
        exs = []
        for i in range(4):
            mu = torch.zeros(1, model.latent_dim)
            log_var = torch.ones(1, log_var)
            z = model.reparameterize(mu, log_var)
            z = (z.unsqueeze(0), z.unsqueeze(0))
            # Teacher forcing here
            SOS = torch.ones(1, 1).long().to(model.device())
            initial, hidden = model.decode(SOS, z, None)

            print(initial.shape)
            _, word = torch.max(initial)
            out_sequence = [corpus.dictionary.idx2word(word.item())]
            while out_sequence[-1] != "<EOS>":
                word = word.unsqueeze(0)
                word, hidden = model.decode(word, z, None)
                out_sequence.append(corpus.dictionary.idx2word(word.item()))
                z = hidden
            exs.append(out_sequence)

        # Produce 4 examples here
        if valid_log is not None:
            for i in range(len(exs):
                name_ = "generated_example_{}".format(i)
                valid_log.add_text(name_, exs[i], global_step)
        else:
            for i in range(len(exs):
                name_ = "generated_example_{}".format(i)
                print(name_, exs[i]))

        avg_l = np.mean(losses)
        print("epoch %-3d \t loss = %0.3f \t" % (epoch, avg_l))
        model.save_model()
    model.save_model()
