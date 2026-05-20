"""
One-shot ingestion: embed the FMCSA starter corpus and load it into pgvector.

Run: python ingest_fmcsa.py
"""

from data.fmcsa_corpus import CORPUS
import vector_store


def main():
    print(f"Embedding {len(CORPUS)} FMCSA passages with voyage-3-large...")
    n = vector_store.ingest(CORPUS)
    print(f"Ingested {n} documents into fmcsa_documents.")

    sample = vector_store.search("how long can I drive after my 10-hour break", k=2)
    print("\nSanity check — top result for 'how long can I drive after my 10-hour break':")
    for hit in sample:
        print(f"  [{hit['score']:.3f}] {hit['title']} ({hit['citation']})")


if __name__ == "__main__":
    main()
