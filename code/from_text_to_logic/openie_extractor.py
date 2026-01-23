#!/usr/bin/env python3
"""
openie_extractor.py - OpenIE Relation Triple Extractor

This module handles Stage 1 of the text-to-logic pipeline:
extracting relation triples from natural language text using Stanford CoreNLP OpenIE.
"""

from typing import List, Dict, Any
from openie import StanfordOpenIE


class OpenIEExtractor:
    """Extracts relation triples from text using Stanford CoreNLP OpenIE."""

    def __init__(self):
        """Initialize the Stanford OpenIE extractor."""
        print("Initializing Stanford OpenIE...")
        try:
            self.openie = StanfordOpenIE()
            print("Stanford OpenIE initialization complete.")
        except Exception as e:
            print(f"Error initializing Stanford OpenIE: {e}")
            raise RuntimeError(f"Failed to initialize Stanford OpenIE: {e}")

    def extract_triples(self, text: str) -> List[Dict[str, Any]]:
        """
        Extract OpenIE relation triples from the input text using Stanford OpenIE.

        Args:
            text (str): Input text to extract relations from

        Returns:
            List[Dict[str, Any]]: List of relation triples with confidence scores
        """
        print("Extracting relation triples using Stanford OpenIE...")
        try:
            # Use Stanford OpenIE to extract triples
            raw_triples = self.openie.annotate(text)

            triples = []
            for triple_data in raw_triples:
                # Stanford OpenIE returns different formats, handle them
                if isinstance(triple_data, dict):
                    # Handle dictionary format
                    subject = triple_data.get('subject', '').strip()
                    predicate = triple_data.get('relation', '').strip()
                    obj = triple_data.get('object', '').strip()
                    confidence = float(triple_data.get('confidence', 1.0))
                elif isinstance(triple_data, (list, tuple)) and len(triple_data) >= 3:
                    # Handle tuple/list format (subject, relation, object)
                    subject = str(triple_data[0]).strip()
                    predicate = str(triple_data[1]).strip()
                    obj = str(triple_data[2]).strip()
                    confidence = float(triple_data[3]) if len(triple_data) > 3 else 1.0
                else:
                    # Handle string format (tab-separated)
                    parts = str(triple_data).strip().split('\t')
                    if len(parts) >= 3:
                        subject = parts[0].strip()
                        predicate = parts[1].strip()
                        obj = parts[2].strip()
                        confidence = float(parts[3]) if len(parts) > 3 and parts[3].replace('.','').isdigit() else 1.0
                    else:
                        continue

                # Filter out empty or very short components
                if len(subject) > 0 and len(predicate) > 0 and len(obj) > 0:
                    triples.append({
                        'subject': subject,
                        'predicate': predicate,
                        'object': obj,
                        'confidence': confidence
                    })

            print(f"Extracted {len(triples)} relation triples from Stanford OpenIE")

            # Log some examples for debugging
            if triples:
                print("Sample triples:")
                for i, triple in enumerate(triples[:3]):
                    print(f"  {i+1}. ({triple['subject']}; {triple['predicate']}; {triple['object']}) [conf: {triple['confidence']:.3f}]")

            return triples

        except Exception as e:
            print(f"Warning: Stanford OpenIE extraction failed: {e}")
            print("Continuing without OpenIE preprocessing...")
            import traceback
            traceback.print_exc()
            return []

    def format_triples(self, triples: List[Dict[str, Any]]) -> str:
        """
        Format OpenIE triples as tab-separated values for downstream processing.

        Args:
            triples (List[Dict[str, Any]]): List of relation triples

        Returns:
            str: Formatted string of triples (one per line, tab-separated)
        """
        if not triples:
            return "No OpenIE triples extracted."

        formatted_lines = []
        for triple in triples:
            line = f"{triple['subject']}\t{triple['predicate']}\t{triple['object']}\t{triple['confidence']:.4f}"
            formatted_lines.append(line)

        return "\n".join(formatted_lines)

    def close(self):
        """Clean up Stanford OpenIE resources."""
        if hasattr(self, 'openie'):
            try:
                # The Stanford OpenIE wrapper handles server cleanup automatically
                # No explicit close method needed
                pass
            except:
                pass

    def __del__(self):
        """Destructor to ensure cleanup."""
        self.close()
