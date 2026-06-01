"""
core/owlxml.py — auto-convert OWL/XML to OWL Functional Syntax.

Prestige's parser (`core.ofn`) reads OWL 2 Functional Syntax only. Many
people save their ontology as OWL/XML (the default in modern Protégé),
also using the `.owl` extension — those files would otherwise fail to
load with a cryptic parse error.

This module recognises OWL/XML byte streams and converts them in-memory
before they hit the Functional-Syntax parser. The four common OWL 2
serialisations look like:

    Functional   :   Prefix(:=<...>)\\nOntology(<...>...
    OWL/XML      :   <?xml ...?>\\n<Ontology xmlns="...owl#"...>
    RDF/XML      :   <?xml ...?>\\n<rdf:RDF ...>
    Turtle / N3  :   @prefix : <...> .

Only OWL/XML can be converted losslessly to Functional Syntax with a
mechanical XML walk. RDF/XML and Turtle aren't supported here — they
need a full OWL 2 axiom-graph reasoner (use owlready2 or ROBOT to
convert those first).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

OWL_NS = "http://www.w3.org/2002/07/owl#"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def is_owl_xml(text: str) -> bool:
    """Heuristic: does `text` look like an OWL/XML document?"""
    head = text.lstrip()[:512]
    if not head.startswith("<?xml") and not head.startswith("<Ontology"):
        return False
    # Peek for the OWL XML root element
    return ("<Ontology" in head and "www.w3.org/2002/07/owl" in head)


def is_rdf_xml(text: str) -> bool:
    """Heuristic: does `text` look like RDF/XML?"""
    head = text.lstrip()[:512]
    return head.startswith("<?xml") and "<rdf:RDF" in head


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def _localname(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_prefixes(root) -> dict:
    out = {}
    for elem in root.findall("{%s}Prefix" % OWL_NS):
        out[elem.get("name", "")] = elem.get("IRI", "")
    return out


def _shorten_iri(iri: Optional[str], prefixes: dict) -> Optional[str]:
    if iri is None:
        return None
    best_pfx, best_iri = None, ""
    for pfx, full in prefixes.items():
        if iri.startswith(full) and len(full) > len(best_iri):
            best_pfx, best_iri = pfx, full
    if best_pfx is not None:
        local = iri[len(best_iri):]
        if local and not any(c in local for c in "<>\"'(){}[] "):
            return "%s:%s" % (best_pfx, local)
    return "<%s>" % iri


def _resolve_iri(elem, prefixes) -> Optional[str]:
    if elem is None:
        return None
    if "IRI" in elem.attrib:
        iri = elem.get("IRI")
        if iri.startswith("#"):
            return prefixes.get("", "") + iri[1:]
        if iri.startswith(("http://", "https://", "urn:")):
            return iri
        return prefixes.get("", "") + iri
    if "abbreviatedIRI" in elem.attrib:
        abbr = elem.get("abbreviatedIRI")
        if ":" in abbr:
            pfx, local = abbr.split(":", 1)
            if pfx in prefixes:
                return prefixes[pfx] + local
        return abbr
    return None


def _render_literal(elem, prefixes) -> str:
    text = (elem.text or "")
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    lang = elem.get("{%s}lang" % XML_NS)
    if lang:
        return '"%s"@%s' % (text, lang)
    dt = elem.get("datatypeIRI")
    if dt:
        return '"%s"^^%s' % (text, _shorten_iri(dt, prefixes))
    return '"%s"' % text


ENTITY_TAGS = {"Class", "ObjectProperty", "DataProperty",
               "NamedIndividual", "AnnotationProperty", "Datatype"}


def _render_arg(elem, prefixes) -> str:
    tag = _localname(elem.tag)
    if tag == "Literal":
        return _render_literal(elem, prefixes)
    if tag == "IRI":
        iri = elem.text or ""
        if iri.startswith("#"):
            iri = prefixes.get("", "") + iri[1:]
        return _shorten_iri(iri, prefixes)
    if tag == "AbbreviatedIRI":
        abbr = elem.text or ""
        if ":" in abbr:
            pfx, local = abbr.split(":", 1)
            if pfx in prefixes:
                return _shorten_iri(prefixes[pfx] + local, prefixes)
        return abbr
    if tag in ENTITY_TAGS:
        iri = _resolve_iri(elem, prefixes)
        return "%s(%s)" % (tag, _shorten_iri(iri, prefixes))
    # Compound expressions → recurse
    inner = " ".join(_render_arg(c, prefixes) for c in elem)
    return "%s(%s)" % (tag, inner)


def _render_axiom(elem, prefixes) -> str:
    tag = _localname(elem.tag)
    children = [c for c in elem if _localname(c.tag) != "Annotation"]
    args = " ".join(_render_arg(c, prefixes) for c in children)
    return "%s(%s)" % (tag, args)


def owlxml_to_ofn(xml_text: str) -> str:
    """Convert an OWL/XML document (as text) to OWL Functional Syntax."""
    root = ET.fromstring(xml_text)
    if _localname(root.tag) != "Ontology":
        raise ValueError("Not an OWL/XML document: root is <%s>" % root.tag)

    prefixes = _parse_prefixes(root)
    onto_iri = root.get("ontologyIRI", "")
    version_iri = root.get("versionIRI")

    out = []
    for name, iri in prefixes.items():
        out.append("Prefix(%s:=<%s>)" % (name, iri))
    out.append("")
    out.append("Ontology(<%s>" % onto_iri)
    if version_iri:
        out.append("<%s>" % version_iri)

    for elem in root:
        if _localname(elem.tag) == "Annotation":
            out.append(_render_axiom(elem, prefixes))

    SKIP = {"Prefix", "Annotation", "Import"}
    for elem in root:
        if _localname(elem.tag) in SKIP:
            continue
        out.append(_render_axiom(elem, prefixes))

    out.append(")")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------

def maybe_convert(text: str) -> str:
    """Return OFN text. If `text` is already Functional Syntax, return
    as-is. If OWL/XML, convert. If RDF/XML or Turtle, raise a clear
    error pointing the user at Protégé."""
    if is_owl_xml(text):
        return owlxml_to_ofn(text)
    if is_rdf_xml(text):
        raise ValueError(
            "This looks like RDF/XML. Prestige reads OWL Functional Syntax "
            "(and auto-converts OWL/XML). Re-save in Protégé via "
            "File → Save As → 'OWL Functional Syntax'.")
    stripped = text.lstrip()
    if stripped.startswith("@prefix") or stripped.startswith("@base"):
        raise ValueError(
            "This looks like Turtle/N3. Prestige reads OWL Functional Syntax. "
            "Re-save in Protégé via File → Save As → 'OWL Functional Syntax'.")
    return text
