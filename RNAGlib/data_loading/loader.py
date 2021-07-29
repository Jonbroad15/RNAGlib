import pickle
import sys
import os
from tqdm import tqdm
import networkx as nx
import dgl
import numpy as np
import torch

import functools
import os
import sys
import collections
from collections import defaultdict
import random
import requests
import warnings
import tarfile
import zipfile

script_dir = os.path.dirname(os.path.realpath(__file__))
if __name__ == "__main__":
    sys.path.append(os.path.join(script_dir, '..'))

from torch.utils.data import Dataset, DataLoader, Subset
from kernels.node_sim import SimFunctionNode, k_block_list, simfunc_from_hparams, EDGE_MAP
from utils import graph_io
from data_loading.feature_maps import build_node_feature_parser

# FEATURE_MAPS = {
#     'nt_code': {k: v for v, k in enumerate(['A', 'U', 'C', 'G', 'P', 'c', 'a', 'u', 't', 'g'])},
#     'nt_name': {k: v for v, k in enumerate(
#         ['A', 'U', 'C', 'G', 'PSU', 'ATP', 'UR3', '2MG', '4OC', 'CCC', 'GDP', 'M2G', '5MC', '7MG', 'MA6', 'GTP', 'G46',
#          'CBV', 'OMG', 'OMU', '5MU', '6MZ', 'RSP', 'G48', 'OMC', 'A44', '4SU', 'U36', 'H2U', 'CM0', 'I', 'C43', '1MA',
#          'A23'])},
#     'form': {k: v for v, k in enumerate(['A', '-', 'B', 'Z', '.', 'x'])},
#     'dbn': {k: v for v, k in enumerate(['(', ')', '{', '}', '<', '>', '&', '.', '[', ']'])},
#     'bb_type': {'--': 0, 'BI': 1, 'BII': 2},
#     'glyco_bond': {'--': 0, 'anti': 1, 'syn': 2},
#     'puckering': {k: v for v, k in enumerate(
#         ["C3'-endo", "C2'-endo", "C3'-exo", "C2'-exo", "C4'-exo", "C1'-exo", "04'-exo", "O4'-endo", "C1'-endo",
#          "C4'-endo", "O4'-exo"])},
#     'sugar_class': {"~C3'-endo": 0, "~C2'-endo": 1, '--': 3},
#     'bin': {k: v for v, k in enumerate(
#         ['33t', '33p', '33m', '32t', '32p', '32m', '23t', '23p', '23m', '22t', '22p', 'inc', 'trig', '22m'])},
#     'cluster': {b: n for n, b in enumerate(
#         ['1a', '1m', '1L', '&a', '7a', '3a', '9a', '1g', '7d', '3d', '5d', '1e', '1c', '1f', '6j', '1b', '1{', '3b',
#          '1z', '5z', '7p', '1t', '5q', '1o', '7r', '2a', '4a', '0a', '#a', '4g', '6g', '8d', '4d', '6d', '2h', '4n',
#          '0i', '6n', '6j', '2{', '4b', '0b', '4p', '6p', '4s', '2o', '5n', '5p', '5r', '3g', '2g', '__', '!!', '1[',
#          '5j', '0k', '2z', '2u', '2['])},
#     'sse': {s: n for s, n in enumerate(['hairpin_1', 'hairpin_3', 'buldge_1'])}
# }

# Make each of those feature maps default to zero
# for feature, feature_map in FEATURE_MAPS.items():
#     default_feature_map = collections.defaultdict(int, feature_map)
#     FEATURE_MAPS[feature] = default_feature_map

# This consists in the keys of the feature map that we consider as not relevant for now.
JUNK_ATTRS = ['index_chain', 'chain_name', 'nt_resnum', 'nt_id', 'nt_type', 'summary', 'C5prime_xyz', 'P_xyz',
              'frame', 'is_modified']

# The annotation fields also should not be included as node features
ANNOTS_ATTRS = ['node_annots', 'edge_annots', 'graphlet_annots']


def dict_union(a, b):
    """
    performs union operation on two dictionaries of sets
    """
    c = {k: a[k].union(b[k]) for k in set(a.keys()).intersection(set(b.keys()))}
    for k in (set(b.keys()) - set(c.keys())):
        c[k] = b[k]
    for k in (set(a.keys()) - set(c.keys())):
        c[k] = a[k]

    for k, v in c.items():
        print(f'\nkey: {k}\tset:')
        print(v)

    print('\nNEXT\n')
    return c


def download(url, path=None, overwrite=True, retries=5, verify_ssl=True, log=True):
    """Download a given URL.

    Codes borrowed from mxnet/gluon/utils.py

    Parameters
    ----------
    url : str
        URL to download.
    path : str, optional
        Destination path to store downloaded file. By default stores to the
        current directory with the same name as in url.
    overwrite : bool, optional
        Whether to overwrite the destination file if it already exists.
        By default always overwrites the downloaded file.
    retries : integer, default 5
        The number of times to attempt downloading in case of failure or non 200 return codes.
    verify_ssl : bool, default True
        Verify SSL certificates.
    log : bool, default True
        Whether to print the progress for download

    Returns
    -------
    str
        The file path of the downloaded file.
    """
    if path is None:
        fname = url.split('/')[-1]
        # Empty filenames are invalid
        assert fname, 'Can\'t construct file-name from this URL. ' \
                      'Please set the `path` option manually.'
    else:
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            fname = os.path.join(path, url.split('/')[-1])
        else:
            fname = path
    assert retries >= 0, "Number of retries should be at least 0"

    if not verify_ssl:
        warnings.warn(
            'Unverified HTTPS request is being made (verify_ssl=False). '
            'Adding certificate verification is strongly advised.')

    if overwrite or not os.path.exists(fname):
        dirname = os.path.dirname(os.path.abspath(os.path.expanduser(fname)))
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        while retries + 1 > 0:
            # Disable pyling too broad Exception
            # pylint: disable=W0703
            try:
                if log:
                    print('Downloading %s from %s...' % (fname, url))
                r = requests.get(url, stream=True, verify=verify_ssl)
                if r.status_code != 200:
                    raise RuntimeError("Failed downloading url %s" % url)
                with open(fname, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024):
                        if chunk:  # filter out keep-alive new chunks
                            f.write(chunk)
                break
            except Exception as e:
                retries -= 1
                if retries <= 0:
                    raise e
                else:
                    if log:
                        print("download failed, retrying, {} attempt{} left"
                              .format(retries, 's' if retries > 1 else ''))

    return fname


def download_name_factory(download_option):
    if download_option == 'test':
        url = 'http://mitra.stanford.edu/kundaje/avanti/rc_data/backup_revcomppaperdata/Gm12878/for_henry/tf_data/Ctcf/foreground.bed.gz'
        dl_path = os.path.join(script_dir, '../data/downloads/test.zip')
        data_path = os.path.join(script_dir, '../data/graphs/')
        dirname = 'test'
        return url, dl_path, data_path, dirname, None
    # Get graphs
    if download_option == 'samples_graphs':
        url = 'toto'
        dl_path = os.path.join(script_dir, '../data/downloads/samples.zip')
        data_path = os.path.join(script_dir, '../data/graphs/')
        dirname = 'samples'

        return url, dl_path, data_path, dirname, None
    if download_option == 'nr_graphs':
        url = 'http://rnaglib.cs.mcgill.ca/static/datasets/glib_nr_graphs.tar.gz'
        dl_path = os.path.join(script_dir, '../data/downloads/glib_nr_graphs.tar.gz')
        data_path = os.path.join(script_dir, '../data/graphs/')
        dirname = 'nr_graphs'
        return url, dl_path, data_path, dirname, None
    if download_option == 'graphs':
        url = 'toto'
        dl_path = os.path.join(script_dir, '../data/downloads/graphs.zip')
        data_path = os.path.join(script_dir, '../data/graphs/')
        dirname = 'graphs'
        return url, dl_path, data_path, dirname, None

    # Get annotations
    if download_option == 'samples_annotated':
        url = 'toto'
        dl_path = os.path.join(script_dir, '../data/downloads/samples_annotated.zip')
        data_path = os.path.join(script_dir, '../data/annotated/')
        dirname = 'samples_annotated'
        hashing_url = 'toto_hash'
        hashing_path = os.path.join(script_dir, '../data/hashing/samples_annotated.p')
        return url, dl_path, data_path, dirname, (hashing_url, hashing_path)
    if download_option == 'nr_annotated':
        url = 'http://rnaglib.cs.mcgill.ca/static/datasets/glib_nr_annot.tar.gz'
        dl_path = os.path.join(script_dir, '../data/downloads/glib_nr_annot.tar.gz')
        data_path = os.path.join(script_dir, '../data/annotated/')
        dirname = 'nr_annotated'
        hashing_url = 'http://rnaglib.cs.mcgill.ca/static/datasets/glib_nr_hashtable.p'
        hashing_path = os.path.join(script_dir, '../data/hashing/nr_annotated.p')
        return url, dl_path, data_path, dirname, (hashing_url, hashing_path)
    if download_option == 'annotated':
        url = 'toto'
        dl_path = os.path.join(script_dir, '../data/downloads/annotated.zip')
        data_path = os.path.join(script_dir, '../data/annotated/')
        dirname = 'annotated'
        hashing_url = 'toto_hash'
        hashing_path = os.path.join(script_dir, '../data/hashing/annotated.p')
        return url, dl_path, data_path, dirname, (hashing_url, hashing_path)
    else:
        raise ValueError(f'The download string command "{download_option}" is not supported. '
                         f'Options should be among : '
                         f'"samples_graphs", "nr_graphs", "graphs", '
                         f'"samples_annotated", "nr_annotated", "annotated"')


class GraphDataset(Dataset):
    def __init__(self,
                 data_path='../data/annotated/samples',
                 edge_map=EDGE_MAP,
                 label='LW',
                 node_simfunc=None,
                 node_features=None,
                 node_target=None,
                 force_undirected=False,
                 verbose=False,
                 download=None):
        """

        :param edge_map: Necessary to build the one hot mapping from edge labels to an id
        :param label: The label to use
        :param node_simfunc: Similarity function defined in kernels/node_sim
        :param data_path: The path of the data. If node_sim is not None, this data should be annotated
        :param force_undirected: Whether we want to force the use of undirected graphs from a directed data set.
        Otherwise the directed attribute is observed from the data at hands.
        :param node_features: node features to include, stored in one tensor in order given by user,
        for example :
        :param node_features: node targets to include, stored in one tensor in order given by user
        """
        if download is not None:
            data_path = self.download(download)
        else:
            if data_path is None:
                raise ValueError('One should provide either a download string command or a data_path')
        self.path = data_path
        self.all_graphs = sorted(os.listdir(data_path))
        if '3p4b_annot.json' in self.all_graphs:
            self.all_graphs.remove('3p4b_annot.json')
        if '2kwg_annot.json' in self.all_graphs:
            self.all_graphs.remove('2kwg_annot.json')

        # This is len() so we have to add the +1
        self.label = label
        self.edge_map = edge_map
        self.num_edge_types = max(self.edge_map.values()) + 1
        if verbose:
            print(f"Found {self.num_edge_types} relations")

        # To ensure that we don't have a discrepancy between the attribute directed and the graphs :
        #   Since the original data is directed, it does not make sense to ask to build directed graphs
        #   from the undirected set.
        #   If directed graphs are what one wants, one should use the directed annotation rather than the undirected.

        # We also need a sample graph to look at the possible node attributes
        # sample_path = os.path.join(self.path, self.all_graphs[0])
        # sample_graph = graph_io.load_json(sample_path)
        # sample_node_attrs = dict()
        # for _, sample_node_attrs in sample_graph.nodes.data():
        #     break
        # self.directed = nx.is_directed(sample_graph)
        # self.force_undirected = force_undirected

        # If it is not None, add a node comparison tool
        self.node_simfunc, self.level = self.add_node_sim(node_simfunc=node_simfunc)

        # If queried, add node features and node targets
        # By default we put all the node info except what is considered as junk
        if node_features == 'all':
            # self.node_features = list(set(sample_node_attrs.keys()) - set(JUNK_ATTRS) - set(ANNOTS_ATTRS))
            self.node_features = None
            self.node_features = ['nt_code']
        else:
            self.node_features = [node_features] if isinstance(node_features, str) else node_features
        self.node_target = [node_target] if isinstance(node_target, str) else node_target

        # Then check that the entries asked for as node features exist in our feature maps and get a parser for each
        # self.node_features_parser = self.build_feature_parser(self.node_features, sample_node_attrs)
        # self.node_target_parser = self.build_feature_parser(self.node_target, sample_node_attrs)

        self.node_features_parser = build_node_feature_parser(self.node_features)
        self.node_target_parser = build_node_feature_parser(self.node_target)

    def download(self, download_option):
        # Get the correct names for the download option and download the correct files
        url, dl_path, data_path, dirname, hashing = download_name_factory(download_option)
        if not os.path.exists(dl_path):
            print('Required dataset not found, launching a download. This should take about a minute')
            download(path=dl_path,
                     url=url)
        if hashing is not None:
            hashing_url, hashing_path = hashing
            if not os.path.exists(hashing_path):
                download(path=hashing_path,
                         url=hashing_url)

        # Expand the compressed files at the right location
        if dl_path.endswith('.zip'):
            with zipfile.ZipFile(dl_path, 'r') as zip_file:
                zip_file.extractall(path=data_path)
        elif '.tar' in url:
            with tarfile.open(dl_path) as tar_file:
                tar_file.extractall(path=data_path)
        full_data_path = os.path.join(data_path, dirname)
        return full_data_path

    def __len__(self):
        return len(self.all_graphs)

    def add_node_sim(self, node_simfunc):
        if node_simfunc is not None:
            if node_simfunc.method in ['R_graphlets', 'graphlet', 'R_ged']:
                level = 'graphlet_annots'
            else:
                level = 'edge_annots'
        else:
            node_simfunc, level = None, None
        return node_simfunc, level

    # def build_feature_parser(self, list_of_features, sample_node_attrs):
    #     """
    #     We build the node feature map from the user input and check that all fields make sense.
    #     This then build parsing functions to deal with each possible outputs
    #
    #     This is added as precomputation step so that we get different errors.
    #     Here we establish a static feature map based on just one graphs.
    #     Failure on other graphs will be deemed as such.
    #
    #     :param list_of_features:
    #     :return:
    #     """
    #     if list_of_features is None:
    #         return None
    #
    #     # print('computing a new parser')
    #     # print(list_of_features)
    #
    #     def floatit(x):
    #         return 0.0 if x is None else float(x)
    #
    #     def bindit(x):
    #         return 0.0 if x is None else 1.0
    #
    #     def lookit(x, feature):
    #         # print("I'm in lookit and feature is ", feature)
    #         return FEATURE_MAPS[feature][x]
    #
    #     for feature in list_of_features:
    #         if not feature in sample_node_attrs:
    #             raise ValueError(f'{feature} was asked for as a node feature/target'
    #                              f'by user but not found in the graph node attributes')
    #
    #     feature_parser = dict()
    #     for local_feature in list_of_features:
    #         feature_value = sample_node_attrs[local_feature]
    #
    #         # print('new feature :')
    #         # print(local_feature)
    #         # print(feature_value)
    #
    #         if isinstance(feature_value, (int, float)):
    #             # We have to add None cases, because sometimes missing values are encoded as None
    #             feature_parser[local_feature] = floatit
    #         elif 'binding' in local_feature:
    #             # print("binding", local_feature)
    #             feature_parser[local_feature] = bindit
    #         elif isinstance(feature_value, str):
    #             # print("other", local_feature)
    #             feature_parser[local_feature] = functools.partial(lookit, feature=str(local_feature))
    #         # print()
    #     return feature_parser

    def get_node_encoding(self, g, encode_feature=True):
        """
        Get targets for graph g
        for every node get the attribute specified by self.node_target
        output a mapping of nodes to their targets
        """
        targets = {}
        node_parser = self.node_features_parser if encode_feature else self.node_target_parser

        if len(node_parser) == 0:
            return None

        # print('using node parser : ', node_parser)
        for node, attrs in g.nodes.data():
            all_node_feature_encoding = list()
            for i, (feature, feature_encoder) in enumerate(node_parser.items()):
                try:
                    node_feature = attrs[feature]
                    node_feature_encoding = feature_encoder.encode(node_feature)
                except KeyError:
                    node_feature_encoding = feature_encoder.encode_default()
                all_node_feature_encoding.append(node_feature_encoding)
            targets[node] = torch.cat(all_node_feature_encoding)
        return targets

    def fix_buggy_edges(self, graph, strategy='remove'):
        """
        Sometimes some edges have weird names such as t.W representing a fuzziness.
        We just remove those as they don't deliver a good information
        :param graph:
        :param strategy: How to deal with it : for now just remove them.
        In the future maybe add an edge type in the edge map ?
        :return:
        """
        if strategy == 'remove':
            # Filter weird edges for now
            to_remove = list()
            for start_node, end_node, nodedata in graph.edges(data=True):
                if nodedata[self.label] not in self.edge_map:
                    to_remove.append((start_node, end_node))
            for start_node, end_node in to_remove:
                graph.remove_edge(start_node, end_node)
        else:
            raise ValueError(f'The edge fixing strategy : {strategy} was not implemented yet')
        return graph

    def __getitem__(self, idx):
        g_path = os.path.join(self.path, self.all_graphs[idx])
        graph = graph_io.load_json(g_path)

        # # We can go from directed to undirected
        # if self.force_undirected:
        #     graph = nx.to_undirected(graph)
        #
        # # This is a weird call but necessary for DGL as it only deals
        # #   with undirected graphs that have both directed edges
        # graph = graph.to_directed()

        graph = self.fix_buggy_edges(graph=graph)

        # Get Edge Labels
        one_hot = {edge: torch.tensor(self.edge_map[label]) for edge, label in
                   (nx.get_edge_attributes(graph, self.label)).items()}
        nx.set_edge_attributes(graph, name='one_hot', values=one_hot)

        # Get Node labels
        node_attrs_toadd = list()
        # if self.node_features is not None:
        if len(self.node_features_parser) > 0:
            feature_encoding = self.get_node_encoding(graph, encode_feature=True)
            nx.set_node_attributes(graph, name='features', values=feature_encoding)
            node_attrs_toadd.append('features')
        if len(self.node_target_parser) > 0:
            target_encoding = self.get_node_encoding(graph, encode_feature=False)
            nx.set_node_attributes(graph, name='target', values=target_encoding)
            node_attrs_toadd.append('target')
        # Careful ! When doing this, the graph nodes get sorted.
        g_dgl = dgl.from_networkx(nx_graph=graph, edge_attrs=['one_hot'],
                                  node_attrs=node_attrs_toadd)

        if self.node_simfunc is not None:
            ring = list(sorted(graph.nodes(data=self.level)))
            return g_dgl, ring
        else:
            return g_dgl, 0


def collate_wrapper(node_simfunc=None, max_size_kernel=None):
    """
        Wrapper for collate function so we can use different node similarities.
        We cannot use functools.partial as it is not picklable so incompatible with Pytorch loading
    """
    if node_simfunc is not None:
        def collate_block(samples):
            # The input `samples` is a list of tuples (graph, ring).
            graphs, rings = map(list, zip(*samples))

            # DGL makes batching by making all small graphs a big one with disconnected components
            # We keep track of those
            batched_graph = dgl.batch(graphs)
            len_graphs = [graph.number_of_nodes() for graph in graphs]

            # Now compute similarities, we need to flatten the list and then use the kernels :
            # The rings is now a list of list of tuples
            # If we have a huge graph, we can sample max_size_kernel nodes to avoid huge computations,
            # We then return the sampled ids
            flat_rings = list()
            node_ids = list()
            for ring in rings:
                if max_size_kernel is None or len(ring) < max_size_kernel:
                    # Just take them all
                    node_ids.extend([1 for _ in ring])
                    flat_rings.extend(ring)
                else:
                    # Take only 'max_size_kernel' elements
                    graph_node_id = [1 for _ in range(max_size_kernel)] + [0 for _ in
                                                                           range(len(ring) - max_size_kernel)]
                    random.shuffle(graph_node_id)
                    node_ids.extend(graph_node_id)
                    flat_rings.extend([node for i, node in enumerate(ring) if graph_node_id[i] == 1])
            K = k_block_list(flat_rings, node_simfunc)
            return batched_graph, torch.from_numpy(K).detach().float(), len_graphs, node_ids
    else:
        def collate_block(samples):
            # The input `samples` is a list of pairs
            #  (graph, label).
            graphs, _ = map(list, zip(*samples))
            batched_graph = dgl.batch(graphs)
            len_graphs = [graph.number_of_nodes() for graph in graphs]
            return batched_graph, len_graphs
    return collate_block


class Loader:
    def __init__(self,
                 dataset,
                 batch_size=5,
                 num_workers=20,
                 max_size_kernel=None,
                 split=True,
                 verbose=False):
        """
        :param batch_size:
        :param num_workers:
        :param node_simfunc: The node comparison object to use for the embeddings. If None is selected,
        will just return graphs
        :param max_graphs: If we use K comptutations, we need to subsamble some nodes for the big graphs
        or else the k computation takes too long
        :param hparams:
        :param node_features: (str list) features to be included in feature tensor
        :param node_target: (str) target attribute for node classification
        """
        self.dataset = dataset
        self.batch_size = batch_size
        self.num_workers = num_workers
        self.max_size_kernel = max_size_kernel
        self.split = split
        self.verbose = verbose

    def get_data(self):
        collate_block = collate_wrapper(self.dataset.node_simfunc, max_size_kernel=self.max_size_kernel)
        if not self.split:
            loader = DataLoader(dataset=self.dataset, shuffle=True, batch_size=self.batch_size,
                                num_workers=self.num_workers, collate_fn=collate_block)
            return loader

        else:
            n = len(self.dataset)
            indices = list(range(n))
            # np.random.shuffle(indices)

            np.random.seed(0)
            split_train, split_valid = 0.7, 0.85
            train_index, valid_index = int(split_train * n), int(split_valid * n)

            train_indices = indices[:train_index]
            valid_indices = indices[train_index:valid_index]
            test_indices = indices[valid_index:]

            train_set = Subset(self.dataset, train_indices)
            valid_set = Subset(self.dataset, valid_indices)
            test_set = Subset(self.dataset, test_indices)

            if self.verbose:
                print(f"training items: ", len(train_set))
            train_loader = DataLoader(dataset=train_set, shuffle=True, batch_size=self.batch_size,
                                      num_workers=self.num_workers, collate_fn=collate_block)
            valid_loader = DataLoader(dataset=valid_set, shuffle=True, batch_size=self.batch_size,
                                      num_workers=self.num_workers, collate_fn=collate_block)
            test_loader = DataLoader(dataset=test_set, shuffle=True, batch_size=self.batch_size,
                                     num_workers=self.num_workers, collate_fn=collate_block)
            return train_loader, valid_loader, test_loader


class InferenceLoader:
    def __init__(self,
                 list_to_predict,
                 data_path,
                 dataset=None,
                 batch_size=5,
                 num_workers=20):
        if dataset is None:
            dataset = GraphDataset(data_path=data_path)
        self.dataset = dataset
        self.dataset.all_graphs = list_to_predict
        self.batch_size = batch_size
        self.num_workers = num_workers

    def get_data(self):
        collate_block = collate_wrapper(None)
        train_loader = DataLoader(dataset=self.dataset,
                                  shuffle=False,
                                  batch_size=self.batch_size,
                                  num_workers=self.num_workers,
                                  collate_fn=collate_block)
        return train_loader


class UnsupervisedLoader(Loader):
    """
    Basically just change the default of the loader based on the usecase
    """

    def __init__(self,
                 dataset=None,
                 **kwargs):
        if dataset is None:
            dataset = GraphDataset(node_simfunc=SimFunctionNode('R_1', 2),
                                   download='nr_annotated')
        super().__init__(
            dataset=dataset,
            **kwargs
        )


class SupervisedLoader(Loader):
    """
    Basically just change the default of the loader based on the usecase
    """

    def __init__(self,
                 dataset=None,
                 **kwargs):
        if dataset is None:
            dataset = GraphDataset(node_target='binding_protein',
                                   download='nr_graphs')
        super().__init__(
            dataset=dataset,
            **kwargs
        )


def loader_from_hparams(data_path, hparams, list_inference=None):
    """
        :params
        :get_sim_mat: switches off computation of rings and K matrix for faster loading.
    """
    if list_inference is None:
        node_simfunc = simfunc_from_hparams(hparams)
        edge_map = hparams.get('edges', 'edge_map')
        dataset = GraphDataset(edge_map=edge_map,
                               node_simfunc=node_simfunc,
                               data_path=data_path)
        loader = Loader(dataset=dataset,
                        batch_size=hparams.get('argparse', 'batch_size'),
                        num_workers=hparams.get('argparse', 'workers'),
                        )
        return loader
    dataset = GraphDataset(data_path=data_path, edge_map=hparams.get('edges', 'edge_map'))
    loader = InferenceLoader(list_to_predict=list_inference,
                             dataset=dataset,
                             batch_size=hparams.get('argparse', 'batch_size'),
                             num_workers=hparams.get('argparse', 'workers'), )
    return loader


if __name__ == '__main__':
    import time

    pass

    # annotated_path = os.path.join(script_dir, '../../data/annotated/undirected')
    # g_path ='2kwg.json'
    # graph = graph_io.load_json(g_path)
    # print(len(graph.nodes()))
    # print(len(graph.edges()))
    #
    # g_path ='3p4b.json'
    # graph = graph_io.load_json(g_path)
    # print(len(graph.nodes()))
    # print(len(graph.edges()))

    # np.random.seed(0)
    # torch.manual_seed(0)
    #
    # annotated_path = os.path.join(script_dir, "..", "data", "annotated", "samples")
    # simfunc_r1 = SimFunctionNode('R_1', 2)
    # loader = Loader(data_path=annotated_path,
    #                 num_workers=2,
    #                 batch_size=1,
    #                 max_size_kernel=100,
    #                 split=False,
    #                 node_simfunc=simfunc_r1,
    #                 node_features=None,
    #                 node_target=None)
    # # node_target=['nt_name', 'nt_code', 'binding_protein'])
    #
    # train_loader = loader.get_data()
    #
    # a = time.time()
    # for i, (graph, K, len_graphs, node_ids) in enumerate(train_loader):
    #     # print('graph :', graph)
    #     # print('K :', K)
    #     # print('length :', len_graphs)
    #     if i > 3:
    #         break
    # print(time.time() - a)
    node_features = ['nt_code', "alpha", "C5prime_xyz", "is_modified"]
    node_target = ['binding_ion']

    # GET THE DATA GOING
    dataset = GraphDataset(node_features=node_features,
                           node_target=node_target,
                           data_path='data/graphs/all_graphs')
    train_loader, validation_loader, test_loader = SupervisedLoader(dataset=dataset, batch_size=1,
                                                                    num_workers=6).get_data()
    print(len(train_loader))
    import time

    time.sleep(2)
    for i, item in enumerate(train_loader):
        # print(item)
        # if i > 100:
        #     break
        if not i % 20: print(i)
        pass

    # loader = SupervisedLoader()
    # loader = UnsupervisedLoader()
