"""
train.py

PURPOSE: This file defines the code for training the neural networks in pytorch
"""

from os import path
import models
import utils
import json
import numpy as np
import random
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.tensorboard as tb
import matplotlib.pyplot as plt
import matplotlib.cm as cmx
import matplotlib.colors as colors

def gaussian_kld(recog_mu, recog_logvar, prior_mu, prior_logvar):
    kld = -0.5 * (1 + (recog_logvar - prior_logvar)
                               - torch.div(torch.pow(prior_mu - recog_mu, 2), torch.exp(prior_logvar))
                               - torch.div(torch.exp(recog_logvar), torch.exp(prior_logvar)))
    return kld


def cvae_loss_function(x_p, x, bow_log, r_mu, r_log_var, p_mu, p_log_var, alpha=0):
    """
    Loss for CVAE is BCE + KLD
        see Appendix B from Kingma and Welling 2014
    Need alpha for KL annealing
    """
    BCE = F.nll_loss(x_p, x, ignore_index=0, reduction="mean")
    bow_log = bow_log.permute(0, 2, 1)
    BOW_LOSS = F.cross_entropy(bow_log, x[:, :-1], ignore_index=0, reduction="mean")
    KLD = gaussian_kld(r_mu, r_log_var, p_mu, p_log_var).mean()
    return BCE + alpha * KLD + BOW_LOSS


def seed_random(rand_seed):
    torch.manual_seed(rand_seed)
    np.random.seed(rand_seed)
    random.seed(rand_seed)


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

# TODO: Fix eval_inference
def eval_inference(model, corpus, valid, valid_log, global_step):
    max_len = 15
    # Change to sample context from test and use this to generate / condition
    device = model.device()
    model.eval()
    avg_loss = 0
    num_examples = 0
    for x, x_len, p, p_len, y, y_len in valid:
        x, x_len = x.to(device), x_len.to(device)
        y, y_len = y.to(device), y_len.to(device)
        if model.name is "cvae":
            p, p_len = p.to(device), p_len.to(device)
            res = model(x, x_len, p, p_len, y, y_len)
        else:
            res = model(x, x_len, y, y_len)
        pred, _, r_mu, r_log_var, p_mu, p_log_var = res

        eos_tensor = torch.empty(x.shape[0], 1).to(device)
        eos_tensor.fill_(corpus.dictionary.word2idx["L"])
        gold = torch.cat([y, eos_tensor], dim=1).long()
        pred = pred.permute(0, 2, 1)
        BCE = F.nll_loss(pred, gold, reduction="sum", ignore_index=0)
        avg_loss += BCE.item()
        num_examples += y.shape[0] # Add how many examples we saw
    if valid_log is not None:
        valid_log.add_scalar("Valid NLL", avg_loss/num_examples, global_step)

    if model.name is "cvae":
        # Now, generate one example
        x, x_len, p, p_len = x[0, :], x_len[0], p[0, :], p_len[0]
        x, x_len = x.unsqueeze(0), x_len.unsqueeze(0),
        p, p_len = p.unsqueeze(0), p_len.unsqueeze(0)
        out_sequence = ["S"]
        hidden = model.infer_hidden(x, x_len, p, p_len)

        # Teacher forcing here
        word = torch.ones([1, 1], dtype=torch.long, device=model.device())
        while out_sequence[-1] != "L" and len(out_sequence) < max_len:
            word = model.embedding(word)
            outputs, hidden = model.decoder(word, hidden)
            outputs = F.log_softmax(model.out(outputs), dim=-1)
            _, word = torch.max(outputs, dim=-1)
            out_sequence.append(corpus.dictionary.idx2word[word.item()])

        if valid_log is not None:
            x_str = [corpus.dictionary.idx2word[word.item()] for word in x[0]]
            p_str = [corpus.dictionary.idx2word[word.item()] for word in p[0]]
            valid_log.add_text("context", str(x_str), global_step)
            valid_log.add_text("persona", str(p_str), global_step)
            valid_log.add_text("generated", str(out_sequence), global_step)
        else:
            x_str = [corpus.dictionary.idx2word[word.item()] for word in x[0]]
            p_str = [corpus.dictionary.idx2word[word.item()] for word in p[0]]
            print("context", x_str)
            print("persona", p_str)
            print("generated", out_sequence)
    model.train()
    return avg_loss/num_examples

def twod_viz(args, model=None):
    device = init_device()
    corpus = utils.Corpus(args.data, args.persona_data)
    if model is None:
        vocab = len(corpus.dictionary)
        model = models.CVAE(vocab, args.embedding, args.hidden, args.latent, rnn=args.rnn)
        model.load_model()
        model = model.to(device)
    model.eval()
    artist_names = ["21 savage", '6ix9ine', 'dr dre', 'earl sweatshirt', 'ice cube', 'kanye west', 'kendrick lamar', 'kid cudi', 'pusha t', 'tyler the creator',]
    artist_list = [2, 5, 23, 26, 36, 44, 46, 47, 67, 86]
    names = {}
    for name, id_ in zip(artist_names, artist_list):
      names[id_] = name
    latents = []
    labels = []
    for artist in artist_list:
        curr = []
        persona = corpus.personas[artist]
        print("Artist {}".format(artist))
        ctxt = ['S']
        ctxt = [corpus.dictionary.word2idx[word] for word in ctxt]
        p_len = torch.tensor([len(persona)]).long().to(device)
        p = torch.tensor([persona]).long().to(device)
        x_len = torch.tensor([len(ctxt)]).long().to(device)
        x = torch.tensor([ctxt]).long().to(device)
        x_emb = model.embedding(x)
        p_emb = model.embedding(p)

        c_enc = model.contextualize(x_emb, x_len, p_emb, p_len)
        out_prior = model.priorlnorm(model.tanh(model.prior(c_enc)))
        p = model.p_mu_log_var(out_prior)
        p_mu, p_log_var = torch.split(p, model.latent_dim, dim=-1)
        latents.append(p_mu.cpu().numpy().squeeze())
        labels.append(artist)
    latents = np.stack(latents)
    means = np.mean(latents, axis=0)
    print(means)
    latents = latents - means
    print(latents)
    print(latents.shape)
    labels = np.array(labels)
    print(labels.shape)
    # cm = plt.get_cmap('gist_rainbow')
    fig = plt.figure()
    # jet = cm = plt.get_cmap('gist_rainbow')
    # cNorm  = colors.Normalize(vmin=0, vmax=10)
    # scalarMap = cmx.ScalarMappable(norm=cNorm, cmap=jet)
    for idx, cl in enumerate(np.unique(labels)):
        plt.scatter(x=latents[idx, 0]*1000,
                    y=latents[idx, 1]*1000,
                    label=artist_names[idx])
        # plt.text(x=latents[idx, 0]*1000,
        #             y=latents[idx, 1]*1000,
        #             s=artist_names[idx],
        #             alpha=0.9,
        #             )
    plt.legend(loc='upper left')
    plt.xlim(-2.75, 2.75)
    plt.ylim(-2.75, 2.75)
    plt.xlabel("Dim 1")
    plt.ylabel("Dim 2")
    plt.title("Artist Embeddings with IDs")
    plt.show()
    fig.savefig('my_figure.png')

def top_p_filtering(logits, top_p=0.9, filter_value=-float('Inf')):
    """ Filter a distribution of logits using top-k and/or nucleus (top-p) filtering
        Modified from: https://gist.github.com/thomwolf/1a5a29f6962089e871b94cbd09daf317
        Args:
            logits: logits distribution shape (vocabulary size)
            top_p >0.0: keep the top tokens with cumulative probability >= top_p (nucleus filtering).
                Nucleus filtering is described in Holtzman et al. (http://arxiv.org/abs/1904.09751)
    """
    assert logits.dim() == 1  # batch size 1 for now - could be updated for more but the code would be less clear
    sorted_logits, sorted_indices = torch.sort(logits, descending=True)
    cumulative_probs = torch.cumsum(F.softmax(sorted_logits, dim=-1), dim=-1)

    # Remove tokens with cumulative probability above the threshold
    sorted_indices_to_remove = cumulative_probs > top_p
    # Shift the indices to the right to keep also the first token above the threshold
    sorted_indices_to_remove[..., 1:] = sorted_indices_to_remove[..., :-1].clone()
    sorted_indices_to_remove[..., 0] = 0

    indices_to_remove = sorted_indices[sorted_indices_to_remove]
    logits[indices_to_remove] = filter_value
    return logits


def gen(args, model=None, max_len=15, top_p=True):
    device = init_device()
    corpus = utils.Corpus(args.data, args.persona_data)
    if model is None:
        vocab = len(corpus.dictionary)
        model = models.CVAE(vocab, args.embedding, args.hidden, args.latent)
        model.load_model()
        model = model.to(device)
    model.eval()

    generated_verses = {}
    for persona in corpus.personas:
        print("Artist {}".format(persona))
        persona_tokens = corpus.personas[persona]
        p_len = torch.tensor([len(persona_tokens)]).long().to(device)
        p = torch.tensor([persona_tokens]).long().to(device)
        # 50 verses per artist, arbitrary
        artist_verses = []
        for _ in range(50):
            generated_verse = []
            ctxt = [1]
            # 16 bars per verse
            for _ in range(16):
                print(ctxt)
                out_sequence = ["S"]
                out_tokens = []
                x_len = torch.tensor([len(ctxt)]).long().to(device)
                x = torch.tensor([ctxt]).long().to(device)
                hidden = model.infer_hidden(x, x_len, p, p_len)
                word = torch.ones([1, 1], dtype=torch.long, device=model.device())
                while out_sequence[-1] != "L" and len(out_sequence) < max_len:
                    word = model.embedding(word)
                    outputs, hidden = model.decoder(word, hidden)
                    outputs = F.log_softmax(model.out(outputs), dim=-1).squeeze()
                    if top_p:
                        outputs = top_p_filtering(outputs).unsqueeze(0)
                    else:
                        outputs = outputs.unsqueeze(0)
                    # Get a random sample from output
                    word = torch.multinomial(F.softmax(outputs, dim=-1), 1)
                    out_tokens.append(word.item())
                    out_sequence.append(corpus.dictionary.idx2word[word.item()])
                ctxt.extend(out_tokens)
                generated_verse.extend(out_sequence)
            artist_verses.append(generated_verse)
        generated_verses[persona] = artist_verses
    with open("verses.json", 'w') as verses_file:
      json.dump(generated_verses, verses_file)


# Computes the perplexity on the validation (hold out) set
def perplexity(args, model=None):
    device = init_device()
    corpus = utils.Corpus(args.data, args.persona_data)
    if model is None:
        vocab = len(corpus.dictionary)
        model = models.VAE(vocab, args.embedding, args.hidden, args.latent)
        model.load_model()
        model = model.to(device)
    model.eval()
    valid = utils.load_data(corpus.valid, batch_size=args.batch_size, num_workers=4)
    print("Beginning eval")
    norm_losses = []
    norm_lens = []
    avg_loss = 0
    num_examples = 0
    for x, x_len, p, p_len, y, y_len in valid:
        x, x_len = x.to(device), x_len.to(device)
        y, y_len = y.to(device), y_len.to(device)
        if model.name is "cvae":
            p, p_len = p.to(device), p_len.to(device)
            res = model(x, x_len, p, p_len, y, y_len)
        else:
            res = model(x, x_len, y, y_len)
        pred = res[0]

        eos_tensor = torch.empty(x.shape[0], 1).to(device)
        eos_tensor.fill_(corpus.dictionary.word2idx["L"])
        gold = torch.cat([y, eos_tensor], dim=1).long()
        pred = pred.permute(0, 2, 1)
        BCE = F.nll_loss(pred, gold, reduction="none", ignore_index=0)
        avg_loss += torch.sum(BCE).item()
        # Norm by length
        norm_losses.append(torch.sum(BCE, dim=-1))
        norm_lens.append(y_len)
        num_examples += y.shape[0]
    exp = torch.cat(norm_losses) / torch.cat(norm_lens)
    ppl = torch.mean(torch.exp(exp)).item()
    avg_loss = avg_loss / num_examples

    print("Validation set had a perplexity of {} and an average NLL of {} per example".format(ppl, avg_loss))


def train(args):
    """
    trains a model as specified by args
    """
    seed_random(args.rand_seed)
    device = init_device()
    train_log, valid_log = init_logger(log_dir=args.log_dir)

    # TODO: set up load_data functions - be best if return a data loader
    corpus = utils.Corpus(args.data, args.persona_data)
    train_data = utils.load_data(corpus.train, batch_size=args.batch_size, num_workers=4)
    test_data = utils.load_data(corpus.test, batch_size=args.batch_size, num_workers=4)

    vocab = len(corpus.dictionary)
    model = models.CVAE(vocab, args.embedding, args.hidden, args.latent)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate,  weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=[100, 150], gamma=0.1)

    if args.continue_training:
        model.load_model()
    model = model.to(device)
    print("Training", model.name, "with #params:", model.parameters)
    loss = cvae_loss_function
    best = float("inf")
    global_step = 0
    for epoch in range(args.num_epoch):
        losses = []
        for x, x_len, p, p_len, y, y_len in train_data:
            # Now we need to make sure everything in the batch has same size
            x, x_len = x.to(device), x_len.to(device)
            p, p_len = p.to(device), p_len.to(device)
            y, y_len = y.to(device), y_len.to(device)
            # Should go from 1 to 0 of ~100k steps (after learned good LM)
            teach = 1 if global_step < 200_000 else .9995
            res = model(x, x_len, p, p_len, y, y_len, teach)
            pred, bow_log, r_mu, r_log_var, p_mu, p_log_var = res

            eos_tensor = torch.empty(x.shape[0], 1).to(device)
            eos_tensor.fill_(corpus.dictionary.word2idx["L"])
            gold = torch.cat([y, eos_tensor], dim=1).long()
            alph = min(max(0, (global_step - 10_000) / 60_000), 1)
            pred = pred.permute(0, 2, 1)
            # Get loss, normalized by batch size
            loss_val = loss(pred, gold, bow_log, r_mu, r_log_var, p_mu, p_log_var, alpha=alph)

            optimizer.zero_grad()
            loss_val.backward()
            if args.grad_clip > 0.0:
                nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            optimizer.step()
            scheduler.step()
            global_step += 1

            losses.append(loss_val.detach().cpu().numpy())
            if train_log is not None:
                train_log.add_scalar("loss", losses[-1], global_step)

        with torch.no_grad():
            validation = eval_inference(model, corpus, test_data, valid_log, global_step)
        avg_l = np.mean(losses)
        print("epoch %-3d \t loss = %0.3f \t" % (epoch, avg_l))
        if validation < best:
            print("Saving model!")
            best = validation
            model.save_model()

    print("Finished training, best model got: {} NLL".format(best))



def vae_train(args):
    """
    trains a model as specified by args
    """
    seed_random(args.rand_seed)
    device = init_device()
    train_log, valid_log = init_logger(log_dir=args.log_dir)

    corpus = utils.Corpus(args.data, args.persona_data)
    train_data = utils.load_data(corpus.train, batch_size=args.batch_size, num_workers=4)
    test_data = utils.load_data(corpus.test, batch_size=args.batch_size, num_workers=4)

    vocab = len(corpus.dictionary)
    model = models.VAE(vocab, args.embedding, args.hidden, args.latent)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate,  weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=[100, 150], gamma=0.1)

    if args.continue_training:
        model.load_model()
    model = model.to(device)
    print("Training", model.name, "with #params:", model.num_params())
    loss = cvae_loss_function
    best = float("inf")
    global_step = 0
    for epoch in range(args.num_epoch):
        losses = []
        for x, x_len, p, p_len, y, y_len in train_data:
            # Now we need to make sure everything in the batch has same size
            x, x_len = x.to(device), x_len.to(device)
            y, y_len = y.to(device), y_len.to(device)
            # Should go from 1 to 0 of ~100k steps (after learned good LM)
            teach = 1 if global_step < 200_000 else .9995
            res = model(x, x_len, y, y_len, teach)
            pred, bow_log, r_mu, r_log_var, p_mu, p_log_var = res

            eos_tensor = torch.empty(x.shape[0], 1).to(device)
            eos_tensor.fill_(corpus.dictionary.word2idx["L"])
            gold = torch.cat([y, eos_tensor], dim=1).long()
            alph = min(max(0, (global_step - 10_000) / 60_000), 1)
            pred = pred.permute(0, 2, 1)
            # Get loss, normalized by batch size
            loss_val = loss(pred, gold, bow_log, r_mu, r_log_var, p_mu, p_log_var, alpha=alph)

            optimizer.zero_grad()
            loss_val.backward()
            if args.grad_clip > 0.0:
                nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            optimizer.step()
            scheduler.step()
            global_step += 1

            losses.append(loss_val.detach().cpu().numpy())
            if train_log is not None:
                train_log.add_scalar("loss", losses[-1], global_step)

        with torch.no_grad():
            validation = eval_inference(model, corpus, test_data, valid_log, global_step)
        avg_l = np.mean(losses)
        print("epoch %-3d \t loss = %0.3f \t" % (epoch, avg_l))
        if validation < best:
            print("Saving model!")
            best = validation
            model.save_model()

    print("Finished training, best model got: {} NLL".format(best))
