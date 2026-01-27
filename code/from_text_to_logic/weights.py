#!/usr/bin/env python3
"""
weights.py - Soft Constraint Verification via LLM Logprobs

Verifies whether a document endorses a soft constraint as a general rule using:
1. Document chunking
2. SBERT retrieval of top-k relevant chunks
3. LLM verification with logprob extraction

See weights_how_it_works.md for detailed algorithm explanation.

Usage (CLI):
    python weights.py document.pdf --constraint "The rule to verify" --api-key sk-...

Usage (Python):
    from from_text_to_logic.weights import verify_constraint

    result = verify_constraint(
        pathfile="document.pdf",
        text_s="Employees must wear safety goggles",
        api_key="sk-..."
    )
    print(f"P(YES) = {result['prob_yes']:.4f}")
"""

import sys
import math
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional

# Add code directory to Python path (for imports to work from anywhere)
script_dir = Path(__file__).resolve().parent
code_dir = script_dir.parent
if str(code_dir) not in sys.path:
    sys.path.insert(0, str(code_dir))

import numpy as np
from openai import OpenAI

# Reuse existing RAG infrastructure
from baseline_rag.chunker import chunk_document
from baseline_rag.retriever import (
    load_sbert_model,
    encode_chunks,
    encode_query,
    compute_cosine_similarity
)


def extract_text_from_document(file_path: str) -> str:
    """
    Extract text from various document formats.

    Args:
        file_path: Path to document file (PDF, DOCX, TXT)

    Returns:
        Extracted text content

    Raises:
        ValueError: If file format is not supported
        FileNotFoundError: If file does not exist
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = path.suffix.lower()

    if suffix in ['.txt', '.text']:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

    elif suffix == '.pdf':
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError(
                "PyMuPDF is required for PDF support. "
                "Install with: pip install PyMuPDF"
            )
        doc = fitz.open(file_path)
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts)

    elif suffix in ['.docx', '.doc']:
        try:
            from docx import Document
        except ImportError:
            raise ImportError(
                "python-docx is required for DOCX support. "
                "Install with: pip install python-docx"
            )
        doc = Document(file_path)
        text_parts = [para.text for para in doc.paragraphs]
        return "\n".join(text_parts)

    else:
        raise ValueError(
            f"Unsupported file format: {suffix}. "
            f"Supported formats: .txt, .pdf, .docx"
        )


def retrieve_top_k_chunks(
    constraint: str,
    chunks: List[Dict],
    sbert_model,
    k: int = 10
) -> List[Dict]:
    """
    Retrieve top-k chunks most similar to the constraint using SBERT.

    Args:
        constraint: The soft constraint text (query)
        chunks: List of chunk dicts from chunker
        sbert_model: Loaded SBERT model
        k: Number of chunks to retrieve

    Returns:
        List of top-k chunks sorted by similarity (highest first)
    """
    chunk_embeddings = encode_chunks(chunks, sbert_model)
    query_embedding = encode_query(constraint, sbert_model)
    similarities = compute_cosine_similarity(query_embedding, chunk_embeddings)

    # Get top-k indices
    k = min(k, len(chunks))
    top_k_indices = np.argsort(similarities)[::-1][:k]

    # Return chunks with similarity scores
    retrieved = []
    for idx in top_k_indices:
        chunk = chunks[idx].copy()
        chunk['similarity'] = float(similarities[idx])
        retrieved.append(chunk)

    return retrieved


def build_verification_prompt(chunks: List[Dict], constraint: str) -> str:
    """
    Build the LLM prompt for YES/NO verification.

    Args:
        chunks: Retrieved chunks (top-k)
        constraint: The soft constraint to verify

    Returns:
        Formatted prompt string
    """
    # Concatenate chunk texts
    chunk_texts = "\n\n".join([chunk['text'] for chunk in chunks])

    prompt = f"""You are a verifier that will answer with exactly one token: "YES" or "NO". Do not produce any other text.

[TEXT]
{chunk_texts}

[CONSTRAINT]
{constraint}

[QUESTION]
Does the text endorse this constraint as a general, necessary rule? Answer "YES" or "NO" with no other words."""

    return prompt


def extract_logprobs_for_yes_no(response) -> Dict[str, float]:
    """
    Extract logprobs for YES and NO tokens from API response.

    Args:
        response: OpenAI API response with logprobs

    Returns:
        Dict with logit_yes, logit_no, prob_yes, prob_no
    """
    # Default to -inf if not found
    logit_yes = -100.0
    logit_no = -100.0

    # Access the first token's logprobs
    if (hasattr(response.choices[0], 'logprobs') and
        response.choices[0].logprobs is not None and
        hasattr(response.choices[0].logprobs, 'content') and
        response.choices[0].logprobs.content):

        first_token_logprobs = response.choices[0].logprobs.content[0]

        # Check the actual generated token first
        actual_token = first_token_logprobs.token.strip().upper()
        actual_logprob = first_token_logprobs.logprob

        if actual_token == "YES":
            logit_yes = actual_logprob
        elif actual_token == "NO":
            logit_no = actual_logprob

        # Search through top_logprobs for YES and NO
        if hasattr(first_token_logprobs, 'top_logprobs') and first_token_logprobs.top_logprobs:
            for candidate in first_token_logprobs.top_logprobs:
                token = candidate.token.strip().upper()
                logprob = candidate.logprob

                if token == "YES" and logit_yes == -100.0:
                    logit_yes = logprob
                elif token == "NO" and logit_no == -100.0:
                    logit_no = logprob

    # Convert logits to probabilities
    prob_yes = math.exp(logit_yes) if logit_yes > -100.0 else 0.0
    prob_no = math.exp(logit_no) if logit_no > -100.0 else 0.0

    return {
        "logit_yes": logit_yes,
        "logit_no": logit_no,
        "prob_yes": prob_yes,
        "prob_no": prob_no
    }


def verify_constraint(
    pathfile: str,
    text_s: str,
    api_key: str,
    model: str = "gpt-4o",
    temperature: float = 0.0,
    max_tokens: int = 5,
    reasoning_effort: str = "low",
    k: int = 10,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    sbert_model_name: str = "all-MiniLM-L6-v2",
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Verify whether a document endorses a soft constraint.

    Args:
        pathfile: Path to document file (PDF, DOCX, TXT)
        text_s: The soft constraint text (natural language)
        api_key: OpenAI API key
        model: OpenAI model (default: gpt-4o)
        temperature: Sampling temperature (default: 0.0)
        max_tokens: Max tokens in response (default: 5)
        reasoning_effort: For reasoning models (default: low)
        k: Number of top chunks to retrieve (default: 10)
        chunk_size: Tokens per chunk (default: 512)
        chunk_overlap: Overlapping tokens between chunks (default: 50)
        sbert_model_name: SBERT model for retrieval (default: all-MiniLM-L6-v2)
        verbose: Print progress messages (default: True)

    Returns:
        Dict containing:
            - logit_yes: Log probability of YES
            - logit_no: Log probability of NO
            - prob_yes: Probability of YES (exp of logit)
            - prob_no: Probability of NO (exp of logit)
            - constraint: The input constraint
            - num_chunks: Total chunks in document
            - chunks_used: Number of chunks sent to LLM
    """
    # Step 1: Extract text from document
    if verbose:
        print(f"Extracting text from: {pathfile}")

    document_text = extract_text_from_document(pathfile)

    if verbose:
        print(f"  Extracted {len(document_text)} characters")

    # Step 2: Chunk the document
    if verbose:
        print(f"Chunking document (size={chunk_size}, overlap={chunk_overlap})...")

    chunks = chunk_document(document_text, chunk_size=chunk_size, overlap=chunk_overlap)

    if verbose:
        print(f"  Created {len(chunks)} chunks")

    # Step 3: Retrieve top-k chunks using SBERT
    if verbose:
        print(f"Loading SBERT model: {sbert_model_name}")

    sbert_model = load_sbert_model(sbert_model_name)

    if verbose:
        print(f"Retrieving top-{k} relevant chunks...")

    retrieved_chunks = retrieve_top_k_chunks(text_s, chunks, sbert_model, k=k)

    if verbose:
        print(f"  Retrieved {len(retrieved_chunks)} chunks")
        print(f"  Top 3 similarities: {[f'{c[\"similarity\"]:.3f}' for c in retrieved_chunks[:3]]}")

    # Step 4: Build verification prompt
    prompt = build_verification_prompt(retrieved_chunks, text_s)

    if verbose:
        print(f"Built verification prompt ({len(prompt)} characters)")

    # Step 5: Call LLM with logprobs
    if verbose:
        print(f"Calling LLM ({model}) for verification...")

    # Initialize OpenAI client
    client = OpenAI(api_key=api_key)

    # Check if this is a reasoning model (logprobs may not be supported)
    is_reasoning_model = any(model.startswith(prefix) for prefix in ["gpt-5", "o1", "o3"])

    if is_reasoning_model:
        print(f"  WARNING: Model {model} may not support logprobs. Consider using gpt-4o.")

    # API call with logprobs
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
        logprobs=True,
        top_logprobs=20  # Get top 20 to ensure YES/NO are captured
    )

    # Step 6: Extract logprobs
    result = extract_logprobs_for_yes_no(response)

    # Add metadata
    result["constraint"] = text_s
    result["num_chunks"] = len(chunks)
    result["chunks_used"] = len(retrieved_chunks)
    result["model"] = model
    result["generated_token"] = response.choices[0].message.content.strip() if response.choices[0].message.content else ""

    if verbose:
        print(f"\nResults:")
        print(f"  Generated token: {result['generated_token']}")
        print(f"  logit(YES) = {result['logit_yes']:.4f}")
        print(f"  logit(NO)  = {result['logit_no']:.4f}")
        print(f"  P(YES) = {result['prob_yes']:.4f}")
        print(f"  P(NO)  = {result['prob_no']:.4f}")

    return result


def main():
    """Command-line interface for constraint verification."""
    parser = argparse.ArgumentParser(
        description="Verify if a document endorses a soft constraint using LLM logprobs",
        epilog="Example: python weights.py document.pdf --constraint \"Safety rule\" --api-key sk-..."
    )
    parser.add_argument(
        "pathfile",
        help="Path to document file (PDF, DOCX, or TXT)"
    )
    parser.add_argument(
        "--constraint", "-c",
        required=True,
        help="The soft constraint to verify (natural language)"
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="OpenAI API key"
    )
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="OpenAI model (default: gpt-4o)"
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (default: 0.0)"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=5,
        help="Maximum tokens in response (default: 5)"
    )
    parser.add_argument(
        "--reasoning-effort",
        default="low",
        choices=["none", "low", "medium", "high"],
        help="Reasoning effort for reasoning models (default: low)"
    )
    parser.add_argument(
        "--k",
        type=int,
        default=10,
        help="Number of top chunks to retrieve (default: 10)"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=512,
        help="Tokens per chunk (default: 512)"
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=50,
        help="Overlapping tokens between chunks (default: 50)"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress messages"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )

    args = parser.parse_args()

    # Validate file path
    if not Path(args.pathfile).exists():
        print(f"Error: File not found: {args.pathfile}")
        return 1

    try:
        result = verify_constraint(
            pathfile=args.pathfile,
            text_s=args.constraint,
            api_key=args.api_key,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            reasoning_effort=args.reasoning_effort,
            k=args.k,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            verbose=not args.quiet
        )

        if args.json:
            import json
            print(json.dumps(result, indent=2))
        else:
            if not args.quiet:
                print("\n" + "=" * 50)
            print(f"Constraint: {result['constraint']}")
            print(f"Generated:  {result['generated_token']}")
            print(f"logit(YES): {result['logit_yes']:.4f}")
            print(f"logit(NO):  {result['logit_no']:.4f}")
            print(f"P(YES):     {result['prob_yes']:.4f}")
            print(f"P(NO):      {result['prob_no']:.4f}")

        return 0

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
