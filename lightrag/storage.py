import asyncio
import html
import os
from dataclasses import dataclass
from typing import Any, Union, cast
import networkx as nx
import numpy as np
from nano_vectordb import NanoVectorDB

from .utils import load_json, logger, write_json
from .base import (
    BaseGraphStorage,
    BaseKVStorage,
    BaseVectorStorage,
)


@dataclass
class JsonKVStorage(BaseKVStorage):
    def __post_init__(self):
        working_dir = self.global_config["working_dir"]
        self._file_name = os.path.join(working_dir, f"kv_store_{self.namespace}.json")
        self._data = load_json(self._file_name) or {}
        logger.info(f"Load KV {self.namespace} with {len(self._data)} data")

    async def all_keys(self) -> list[str]:
        return list(self._data.keys())

    async def index_done_callback(self):
        write_json(self._data, self._file_name)

    async def get_by_id(self, id):
        return self._data.get(id, None)

    async def get_by_ids(self, ids, fields=None):
        if fields is None:
            return [self._data.get(id, None) for id in ids]
        return [
            (
                {k: v for k, v in self._data[id].items() if k in fields}
                if self._data.get(id, None)
                else None
            )
            for id in ids
        ]

    async def filter_keys(self, data: list[str]) -> set[str]:
        return set([s for s in data if s not in self._data])

    async def upsert(self, data: dict[str, dict]):
        left_data = {k: v for k, v in data.items() if k not in self._data}
        self._data.update(left_data)
        return left_data

    async def drop(self):
        self._data = {}


@dataclass
class NanoVectorDBStorage(BaseVectorStorage):
    cosine_better_than_threshold: float = 0.2

    def __post_init__(self):
        self._client_file_name = os.path.join(
            self.global_config["working_dir"], f"vdb_{self.namespace}.json"
        )
        self._max_batch_size = self.global_config["embedding_batch_num"]
        self._client = NanoVectorDB(
            self.embedding_func.embedding_dim, storage_file=self._client_file_name
        )
        self.cosine_better_than_threshold = self.global_config.get(
            "cosine_better_than_threshold", self.cosine_better_than_threshold
        )

    async def upsert(self, data: dict[str, dict]):
        logger.info(f"Inserting {len(data)} vectors to {self.namespace}")
        if not len(data):
            logger.warning("You insert an empty data to vector DB")
            return []
        list_data = [
            {
                "__id__": k,
                **{k1: v1 for k1, v1 in v.items() if k1 in self.meta_fields},
            }
            for k, v in data.items()
        ]
        contents = [v["content"] for v in data.values()]
        batches = [
            contents[i : i + self._max_batch_size]
            for i in range(0, len(contents), self._max_batch_size)
        ]
        embeddings_list = await asyncio.gather(
            *[self.embedding_func(batch) for batch in batches]
        )
        embeddings = np.concatenate(embeddings_list)
        for i, d in enumerate(list_data):
            d["__vector__"] = embeddings[i]
        results = self._client.upsert(datas=list_data)
        return results
    

@dataclass
class PineConeVectorDBStorage(BaseVectorStorage):
    cosine_better_than_threshold: float = 0.2

    def __post_init__(self):
        self._client_file_name = os.path.join(
            self.global_config["working_dir"], f"vdb_{self.namespace}.json"
        )
        self._max_batch_size = self.global_config["embedding_batch_num"]
        self._client = NanoVectorDB(
            self.embedding_func.embedding_dim, storage_file=self._client_file_name
        )
        import os
        from pinecone import Pinecone

        pc = Pinecone() #api_key=os.environ.get('PINECONE_API_KEY'))
        # From here on, everything is identical to the REST-based SDK.
        self._client = pc.Index(host=self._client_pinecone_host)#'my-index-8833ca1.svc.us-east1-gcp.pinecone.io')

        self.cosine_better_than_threshold = self.global_config.get(
            "cosine_better_than_threshold", self.cosine_better_than_threshold
        )

    async def upsert(self, data: dict[str, dict]):
        logger.info(f"Inserting {len(data)} vectors to {self.namespace}")
        if not len(data):
            logger.warning("You insert an empty data to vector DB")
            return []
        list_data = [
            {
                "__id__": k,
                **{k1: v1 for k1, v1 in v.items() if k1 in self.meta_fields},
            }
            for k, v in data.items()
        ]
        contents = [v["content"] for v in data.values()]
        batches = [
            contents[i : i + self._max_batch_size]
            for i in range(0, len(contents), self._max_batch_size)
        ]
        embeddings_list = await asyncio.gather(
            *[self.embedding_func(batch) for batch in batches]
        )
        embeddings = np.concatenate(embeddings_list)
        for i, d in enumerate(list_data):
            d["__vector__"] = embeddings[i]
            # self._client.upsert(vectors=[]) pinecone
        results = self._client.upsert(datas=list_data)
        return results

    async def query(self, query: str, top_k=5):
        embedding = await self.embedding_func([query])
        embedding = embedding[0]
        # self._client.query(vector=[...], top_key=10) pinecone
        results = self._client.query(
            vector=embedding,
            top_k=top_k,
            better_than_threshold=self.cosine_better_than_threshold, ???
        )
        results = [
            {**dp, "id": dp["__id__"], "distance": dp["__metrics__"]} for dp in results
        ]
        return results

    async def index_done_callback(self):
        print("self._client.save()")
        # self._client.save()


@dataclass
class NetworkXStorage(BaseGraphStorage):
    @staticmethod
    def load_nx_graph(file_name) -> nx.Graph:
        if os.path.exists(file_name):
            return nx.read_graphml(file_name)
        return None

    @staticmethod
    def write_nx_graph(graph: nx.Graph, file_name):
        logger.info(
            f"Writing graph with {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges"
        )
        nx.write_graphml(graph, file_name)

    @staticmethod
    def stable_largest_connected_component(graph: nx.Graph) -> nx.Graph:
        """Refer to https://github.com/microsoft/graphrag/index/graph/utils/stable_lcc.py
        Return the largest connected component of the graph, with nodes and edges sorted in a stable way.
        """
        from graspologic.utils import largest_connected_component

        graph = graph.copy()
        graph = cast(nx.Graph, largest_connected_component(graph))
        node_mapping = {
            node: html.unescape(node.upper().strip()) for node in graph.nodes()
        }  # type: ignore
        graph = nx.relabel_nodes(graph, node_mapping)
        return NetworkXStorage._stabilize_graph(graph)

    @staticmethod
    def _stabilize_graph(graph: nx.Graph) -> nx.Graph:
        """Refer to https://github.com/microsoft/graphrag/index/graph/utils/stable_lcc.py
        Ensure an undirected graph with the same relationships will always be read the same way.
        """
        fixed_graph = nx.DiGraph() if graph.is_directed() else nx.Graph()

        sorted_nodes = graph.nodes(data=True)
        sorted_nodes = sorted(sorted_nodes, key=lambda x: x[0])

        fixed_graph.add_nodes_from(sorted_nodes)
        edges = list(graph.edges(data=True))

        if not graph.is_directed():

            def _sort_source_target(edge):
                source, target, edge_data = edge
                if source > target:
                    temp = source
                    source = target
                    target = temp
                return source, target, edge_data

            edges = [_sort_source_target(edge) for edge in edges]

        def _get_edge_key(source: Any, target: Any) -> str:
            return f"{source} -> {target}"

        edges = sorted(edges, key=lambda x: _get_edge_key(x[0], x[1]))

        fixed_graph.add_edges_from(edges)
        return fixed_graph

    def __post_init__(self):
        self._graphml_xml_file = os.path.join(
            self.global_config["working_dir"], f"graph_{self.namespace}.graphml"
        )
        preloaded_graph = NetworkXStorage.load_nx_graph(self._graphml_xml_file)
        if preloaded_graph is not None:
            logger.info(
                f"Loaded graph from {self._graphml_xml_file} with {preloaded_graph.number_of_nodes()} nodes, {preloaded_graph.number_of_edges()} edges"
            )
        self._graph = preloaded_graph or nx.Graph()
        self._node_embed_algorithms = {
            "node2vec": self._node2vec_embed,
        }

    async def index_done_callback(self):
        NetworkXStorage.write_nx_graph(self._graph, self._graphml_xml_file)

    async def has_node(self, node_id: str) -> bool:
        return self._graph.has_node(node_id)

    async def has_edge(self, source_node_id: str, target_node_id: str) -> bool:
        return self._graph.has_edge(source_node_id, target_node_id)

    async def get_node(self, node_id: str) -> Union[dict, None]:
        return self._graph.nodes.get(node_id)

    async def node_degree(self, node_id: str) -> int:
        return self._graph.degree(node_id)

    async def edge_degree(self, src_id: str, tgt_id: str) -> int:
        return self._graph.degree(src_id) + self._graph.degree(tgt_id)

    async def get_edge(
        self, source_node_id: str, target_node_id: str
    ) -> Union[dict, None]:
        return self._graph.edges.get((source_node_id, target_node_id))

    async def get_node_edges(self, source_node_id: str):
        if self._graph.has_node(source_node_id):
            return list(self._graph.edges(source_node_id))
        return None

    async def upsert_node(self, node_id: str, node_data: dict[str, str]):
        self._graph.add_node(node_id, **node_data)

    async def upsert_edge(
        self, source_node_id: str, target_node_id: str, edge_data: dict[str, str]
    ):
        self._graph.add_edge(source_node_id, target_node_id, **edge_data)

    async def embed_nodes(self, algorithm: str) -> tuple[np.ndarray, list[str]]:
        if algorithm not in self._node_embed_algorithms:
            raise ValueError(f"Node embedding algorithm {algorithm} not supported")
        return await self._node_embed_algorithms[algorithm]()

    async def _node2vec_embed(self):
        from graspologic import embed

        embeddings, nodes = embed.node2vec_embed(
            self._graph,
            **self.global_config["node2vec_params"],
        )

        nodes_ids = [self._graph.nodes[node_id]["id"] for node_id in nodes]
        return embeddings, nodes_ids


@dataclass
class Neo4JStorage(BaseGraphStorage):
    @staticmethod
    def load_nx_graph(file_name) -> nx.Graph:
        if os.path.exists(file_name):
            return nx.read_graphml(file_name)
        return None

    # @staticmethod
    # def write_nx_graph(graph: nx.Graph, file_name):
    #     logger.info(
    #         f"Writing graph with {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges"
    #     )
    #     nx.write_graphml(graph, file_name)

  
    def __post_init__(self):
        self._graphml_xml_file = os.path.join(
            self.global_config["working_dir"], f"graph_{self.namespace}.graphml"
        )
        preloaded_graph = NetworkXStorage.load_nx_graph(self._graphml_xml_file)
        if preloaded_graph is not None:
            logger.info(
                f"Loaded graph from {self._graphml_xml_file} with {preloaded_graph.number_of_nodes()} nodes, {preloaded_graph.number_of_edges()} edges"
            )
        self._graph = preloaded_graph or nx.Graph()
        self._node_embed_algorithms = {
            "node2vec": self._node2vec_embed,
        }

    async def index_done_callback(self):
        print ("KG successfully indexed.")
        # Neo4JStorage.write_nx_graph(self._graph, self._graphml_xml_file)
    async def has_node(self, node_id: str) -> bool:
        entity_name_label = node_id
        with self.driver.session() as session:  
            return session.read_transaction(self._check_node_exists, entity_name_label)

        @staticmethod  
        def _check_node_exists(tx, label):  
            query = f"MATCH (n:{label}) RETURN count(n) > 0 AS node_exists"  
            result = tx.run(query)  
            return result.single()["node_exists"]
        
    async def has_edge(self, source_node_id: str, target_node_id: str) -> bool:
        entity_name_label_source = source_node_id
        entity_name_label_target = target_node_id
        #hard code relaitionship type 
        with self.driver.session() as session:  
                result = session.read_transaction(self._check_edge_existence, entity_name_label_source, entity_name_label_target)  
                return result

        @staticmethod  
        def _check_edge_existence(tx, label1, label2):  
            query = (  
                f"MATCH (a:{label1})-[r]-(b:{label2}) "  
                "RETURN COUNT(r) > 0 AS edgeExists"  
            )  
            result = tx.run(query)  
            return result.single()["edgeExists"]
        def close(self):  
            self.driver.close()      
        


    async def get_node(self, node_id: str) -> Union[dict, None]:
        entity_name_label = node_id
        with driver.session() as session:
            result = session.run(
                "MATCH (n) WHERE n.name = $name RETURN n",
                name=node_name
            )

            for record in result:
                return record["n"]  # Return the first matching node
            


    async def node_degree(self, node_id: str) -> int:
       entity_name_label = node_id
       neo4j = Neo4j("bolt://localhost:7687", "neo4j", "password")
       with neo4j.driver.session() as session:
        degree = Neo4j.find_node_degree(session, entity_name_label)
        return degree

        @staticmethod  
        def find_node_degree(session, label):  
            with session.begin_transaction() as tx:  
                result = tx.run("MATCH (n:`{label}`) RETURN n, size((n)--()) AS degree".format(label=label))  
                record = result.single()  
                if record:  
                    return record["degree"]  
                else:  
                    return None

    # edge_degree
        # from neo4j import GraphDatabase
    async def edge_degree(self, src_id: str, tgt_id: str) -> int:
        entity_name__label_source = src_id
        entity_name_label_target = tgt_id
        with graph_db.session() as session:
            result = session.run(
                """MATCH (n1:{node_label1})-[r]-(n2:{node_label2})
                RETURN count(r) AS degree"""
                .format(node_label1=node_label1, node_label2=node_label2)
            )        
            record = result.single()        
            return record["degree"]
    # driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password"))

    #
    # 
    #  def edge_degree(tx, source_id, target_id):
    #     result = tx.run("""
    #         MATCH (source) WHERE ID(source) = $source_id
    #         MATCH (target) WHERE ID(target) = $target_id
    #         MATCH (source)-[r]-(target)
    #         RETURN COUNT(r) AS degree
    #     """, source_id=source_id, target_id=target_id)

    #     return result.single()["degree"]

    # with driver.session() as session:
    #     degree = session.read_transaction(get_edge_degree, 1, 2)
    #     print("Degree of edge between source and target:", degree)

    
    async def get_edge(self, source_node_id: str, target_node_id: str) -> Union[dict, None]:
        entity_name__label_source = src_id
        entity_name_label_target = tgt_id
        """
        Find all edges between nodes of two given labels
        
        Args:
            source_node_label (str): Label of the source nodes
            target_node_label (str): Label of the target nodes
            
        Returns:
            list: List of all relationships/edges found
        """
        with self.driver.session() as session:
            query = f"""
            MATCH (source:{entity_name__label_source})-[r]-(target:{entity_name_label_target})
            RETURN r
            """
            
            result = session.run(query)
            return [record["r"] for record in result]


#upsert_node
    async def upsert_node(self, node_id: str, node_data: dict[str, str]):
        label = node_id
        properties = node_data
        """
        Upsert a node with the given label and properties within a transaction.
        If a node with the same label exists, it will:
        - Update existing properties with new values
        - Add new properties that don't exist
        If no node exists, creates a new node with all properties.
        
        Args:
            label: The node label to search for and apply
            properties: Dictionary of node properties
            
        Returns:
            Dictionary containing the node's properties after upsert, or None if operation fails
        """
        with self.driver.session() as session:
            # Execute the upsert within a transaction
            result = session.execute_write(
                self._do_upsert,
                label,
                properties
            )
            return result
    

        @staticmethod
        def _do_upsert(tx: Transaction, label: str, properties: Dict[str, Any]):
            """
            Static method to perform the actual upsert operation within a transaction
            
            Args:
                tx: Neo4j transaction object
                label: The node label to search for and apply
                properties: Dictionary of node properties
                
            Returns:
                Dictionary containing the node's properties after upsert, or None if operation fails
            """
            # Create the dynamic property string for SET clause
            property_string = ", ".join([
                f"n.{key} = ${key}" 
                for key in properties.keys()
            ])
            
            # Cypher query that either matches existing node or creates new one
            query = f"""
            MATCH (n:{label})
            WITH n LIMIT 1
            CALL {{
                WITH n
                WHERE n IS NOT NULL
                SET {property_string}
                RETURN n
                UNION
                WITH n
                WHERE n IS NULL
                CREATE (n:{label})
                SET {property_string}
                RETURN n
            }}
            RETURN n
            """
        
        # Execute the query with properties as parameters
        result = tx.run(query, properties)
        record = result.single()
        
        if record:
            return dict(record["n"])
        return None
                
   

    async def upsert_edge(self, source_node_id: str, target_node_id: str, edge_data: dict[str, str]) -> None:
        source_node_label = source_node_id
        target_node_label = target_node_id
        """
        Upsert an edge and its properties between two nodes identified by their labels.
        
        Args:
            source_node_label (str): Label of the source node (used as identifier)
            target_node_label (str): Label of the target node (used as identifier)
            edge_properties (dict): Dictionary of properties to set on the edge
        """
        with self._driver.session() as session:
            session.execute_write(
                self._do_upsert_edge,
                source_node_label,
                target_node_label,
                edge_data
            )

        @staticmethod
        def _do_upsert_edge(tx, source_node_label: str, target_node_label: str, edge_properties: Dict[str, Any]) -> None:
            """
            Static method to perform the edge upsert within a transaction.
            
            The query will:
            1. Match the source and target nodes by their labels
            2. Merge the DIRECTED relationship
            3. Set all properties on the relationship, updating existing ones and adding new ones
            """
            # Convert edge properties to Cypher parameter string
            props_string = ", ".join(f"r.{key} = ${key}" for key in edge_properties.keys())
            
            query = """
            MATCH (source)
            WHERE source.label = $source_node_label
            MATCH (target)
            WHERE target.label = $target_node_label
            MERGE (source)-[r:DIRECTED]->(target)
            SET {}
            """.format(props_string)

            # Prepare parameters dictionary
            params = {
                "source_node_label": source_node_label,
                "target_node_label": target_node_label,
                **edge_properties
            }
            
            # Execute the query
            tx.run(query, params)


async def _node2vec_embed(self):
    # async def _node2vec_embed(self):
    with driver.session() as session:
        #Define the Cypher query
        options = self.global_config["node2vec_params"]
        query = f"""CALL gds.node2vec.stream('myGraph', {**options})
                YIELD nodeId, embedding 
                RETURN nodeId, embedding"""
    # Run the query and process the results
    results = session.run(query)
    for record in results:
      node_id = record["nodeId"]
      embedding = record["embedding"]
      print(f"Node ID: {node_id}, Embedding: {embedding}")
      #need to return two lists here.



