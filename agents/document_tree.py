"""
Document Tree Parser — breaks content into hierarchical sections before review.

Instead of treating content as flat text, this builds a tree structure:
- Document
  ├─ Headline/Title (H1)
  ├─ Hero Section (above first subheading)
  ├─ Section 1 (H2)
  │  ├─ Subsection 1.1 (H3)
  │  │  ├─ Paragraph
  │  │  ├─ Bullet list
  │  │  └─ CTA
  │  └─ Subsection 1.2
  └─ Section 2

Benefits:
- AI flags violations with STRUCTURAL CONTEXT ("high-risk claim in headline" vs "same claim in FAQ")
- Fixes respect structure ("remove from headline" vs "qualify in body")
- Pattern learning is granular ("speed claims in bullets = 8x higher risk than in footer")
- Format preservation is automatic (tree maintains original structure)
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class SectionType(Enum):
    """Document section types for compliance context"""
    HEADLINE = "headline"           # H1, main title
    HERO = "hero"                   # Above-the-fold copy (before first H2)
    SECTION = "section"             # Main sections (H2)
    SUBSECTION = "subsection"       # Subsections (H3)
    DEEP_SUBSECTION = "deep"        # Nested deep (H4+)
    BULLET_LIST = "bullet_list"     # Unordered list
    NUMBERED_LIST = "numbered_list" # Ordered list
    CTA = "cta"                     # Call-to-action boxes
    FOOTER = "footer"               # Bottom of page
    VISUAL = "visual"               # [VISUAL: ...] blocks
    TABLE = "table"                 # Markdown tables
    DISCLOSURE = "disclosure"       # Disclosure statements
    FAQ = "faq"                      # FAQ section (detected by "FAQ" or "Questions" in heading)
    SIDEBAR = "sidebar"             # Sidebar/callout boxes
    PARAGRAPH = "paragraph"         # Regular body text


@dataclass
class TreeNode:
    """Single node in document tree"""
    section_type: SectionType
    level: int  # Nesting depth (0=root, 1=H1, 2=H2, etc.)
    heading: Optional[str] = None
    content: str = ""  # Raw text for this node
    start_line: int = 0  # Line number in original document
    end_line: int = 0
    children: list['TreeNode'] = field(default_factory=list)
    parent: Optional['TreeNode'] = None
    risk_context: str = ""  # Why this section is high/low risk
    
    def __repr__(self) -> str:
        indent = "  " * self.level
        heading_str = f" — {self.heading[:40]}" if self.heading else ""
        return f"{indent}{self.section_type.value}{heading_str} (lines {self.start_line}-{self.end_line})"
    
    def add_child(self, child: 'TreeNode') -> 'TreeNode':
        """Add child node and set parent reference"""
        child.parent = self
        self.children.append(child)
        return child
    
    def path(self) -> str:
        """Return breadcrumb path for context"""
        path_parts = []
        node = self
        while node:
            if node.heading:
                path_parts.append(node.heading[:30])
            node = node.parent
        return " > ".join(reversed(path_parts))
    
    def flatten(self) -> list['TreeNode']:
        """Return all leaf nodes (nodes with actual content)"""
        if not self.children:
            return [self]
        result = []
        for child in self.children:
            result.extend(child.flatten())
        return result


class DocumentTree:
    """Build hierarchical tree from document"""
    
    def __init__(self, content: str):
        self.raw_content = content
        self.lines = content.split('\n')
        self.root = TreeNode(SectionType.PARAGRAPH, level=0, content="ROOT")
        self.current_node = self.root
        self._build_tree()
    
    def _build_tree(self):
        """Parse content into tree structure"""
        in_bullet_list = False
        in_numbered_list = False
        in_table = False
        current_paragraph = []
        paragraph_start = 0
        
        for line_idx, line in enumerate(self.lines):
            stripped = line.strip()
            
            # ── Headings ──
            if heading_match := re.match(r'^(#{1,6})\s+(.+)$', stripped):
                # Flush paragraph
                if current_paragraph:
                    self._flush_paragraph(current_paragraph, paragraph_start, line_idx)
                    current_paragraph = []
                
                # Flush lists/tables
                in_bullet_list = False
                in_numbered_list = False
                in_table = False
                
                # Create heading node
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip('*').strip()
                self._add_heading(heading_text, level, line_idx)
            
            # ── Bullet list ──
            elif stripped.startswith(('- ', '• ', '* ')):
                if current_paragraph:
                    self._flush_paragraph(current_paragraph, paragraph_start, line_idx)
                    current_paragraph = []
                
                if not in_bullet_list:
                    in_bullet_list = True
                    bullet_content = []
                    bullet_start = line_idx
                
                bullet_content.append(stripped[2:])
                
                # Check if next line is also a bullet
                if line_idx + 1 >= len(self.lines) or not self.lines[line_idx + 1].strip().startswith(('- ', '• ', '* ')):
                    bullet_node = self._create_node(
                        SectionType.BULLET_LIST,
                        level=self.current_node.level + 1,
                        content='\n'.join(bullet_content),
                        start_line=bullet_start,
                        end_line=line_idx
                    )
                    in_bullet_list = False
            
            # ── Numbered list ──
            elif re.match(r'^\d+[\.\)]\s', stripped):
                if current_paragraph:
                    self._flush_paragraph(current_paragraph, paragraph_start, line_idx)
                    current_paragraph = []
                
                if not in_numbered_list:
                    in_numbered_list = True
                    numbered_content = []
                    numbered_start = line_idx
                
                numbered_content.append(re.sub(r'^\d+[\.\)]\s+', '', stripped))
                
                if line_idx + 1 >= len(self.lines) or not re.match(r'^\d+[\.\)]\s', self.lines[line_idx + 1].strip()):
                    num_node = self._create_node(
                        SectionType.NUMBERED_LIST,
                        level=self.current_node.level + 1,
                        content='\n'.join(numbered_content),
                        start_line=numbered_start,
                        end_line=line_idx
                    )
                    in_numbered_list = False
            
            # ── Tables ──
            elif stripped.startswith('|') and stripped.endswith('|'):
                if current_paragraph:
                    self._flush_paragraph(current_paragraph, paragraph_start, line_idx)
                    current_paragraph = []
                
                if not in_table:
                    in_table = True
                    table_content = []
                    table_start = line_idx
                
                table_content.append(stripped)
                
                if line_idx + 1 >= len(self.lines) or not (self.lines[line_idx + 1].strip().startswith('|')):
                    table_node = self._create_node(
                        SectionType.TABLE,
                        level=self.current_node.level + 1,
                        content='\n'.join(table_content),
                        start_line=table_start,
                        end_line=line_idx
                    )
                    in_table = False
            
            # ── Visual/disclosure blocks ──
            elif stripped.startswith('[VISUAL:'):
                if current_paragraph:
                    self._flush_paragraph(current_paragraph, paragraph_start, line_idx)
                    current_paragraph = []
                
                visual_node = self._create_node(
                    SectionType.VISUAL,
                    level=self.current_node.level + 1,
                    content=stripped,
                    start_line=line_idx,
                    end_line=line_idx
                )
            
            elif stripped.startswith(('ⓘ ', '[DISCLOSURE:', '[REQUIRED DISCLOSURE:', 'Results not guaranteed')):
                if current_paragraph:
                    self._flush_paragraph(current_paragraph, paragraph_start, line_idx)
                    current_paragraph = []
                
                disclosure_node = self._create_node(
                    SectionType.DISCLOSURE,
                    level=self.current_node.level + 1,
                    content=stripped,
                    start_line=line_idx,
                    end_line=line_idx,
                )
                disclosure_node.risk_context = "Required disclosure — must be present and proximate"
            
            # ── Empty lines ──
            elif not stripped:
                if current_paragraph:
                    self._flush_paragraph(current_paragraph, paragraph_start, line_idx)
                    current_paragraph = []
            
            # ── Regular paragraph ──
            else:
                if not current_paragraph:
                    paragraph_start = line_idx
                current_paragraph.append(stripped)
        
        # Flush remaining
        if current_paragraph:
            self._flush_paragraph(current_paragraph, paragraph_start, len(self.lines) - 1)
    
    def _add_heading(self, text: str, level: int, line_idx: int):
        """Add heading and adjust tree depth"""
        # Detect section type by heading content
        section_type = SectionType.SECTION if level == 2 else SectionType.SUBSECTION if level == 3 else SectionType.DEEP_SUBSECTION
        
        if level == 1:
            section_type = SectionType.HEADLINE
        
        # Special detection
        if any(x in text.lower() for x in ['faq', 'questions', 'q&a']):
            section_type = SectionType.FAQ
        elif any(x in text.lower() for x in ['footer', 'contact', 'disclaimer']):
            section_type = SectionType.FOOTER
        
        node = self._create_node(
            section_type,
            level=level,
            heading=text,
            content=text,
            start_line=line_idx,
            end_line=line_idx
        )
        node.risk_context = self._get_risk_context(section_type, text)
        
        # Adjust current node depth
        while self.current_node.level >= level and self.current_node.parent:
            self.current_node = self.current_node.parent
    
    def _flush_paragraph(self, para_lines: list, start: int, end: int):
        """Create paragraph node"""
        para_node = self._create_node(
            SectionType.PARAGRAPH,
            level=self.current_node.level + 1,
            content=' '.join(para_lines),
            start_line=start,
            end_line=end
        )
    
    def _create_node(
        self,
        section_type: SectionType,
        level: int,
        content: str,
        start_line: int,
        end_line: int,
        heading: Optional[str] = None
    ) -> TreeNode:
        """Create node and attach to current parent"""
        node = TreeNode(
            section_type=section_type,
            level=level,
            heading=heading,
            content=content,
            start_line=start_line,
            end_line=end_line,
        )
        self.current_node.add_child(node)
        self.current_node = node
        return node
    
    def _get_risk_context(self, section_type: SectionType, text: str) -> str:
        """Determine risk context based on section type"""
        contexts = {
            SectionType.HEADLINE: "HIGH — Consumer-facing headline. Outcome claims here are most visible.",
            SectionType.HERO: "HIGH — Above-the-fold, first impression. Net impression formed here.",
            SectionType.BULLET_LIST: "HIGH — Consumers scan bullets. Claims here are dominant message.",
            SectionType.CTA: "HIGH — Direct call-to-action. Implied guarantees most problematic.",
            SectionType.SECTION: "MODERATE — Section intro, but secondary to headline.",
            SectionType.FAQ: "MODERATE — Expected to contain Q&A, but claims must still be qualified.",
            SectionType.FOOTER: "LOW — Footer/legal section. Lower consumer attention.",
            SectionType.DISCLOSURE: "CRITICAL — Required disclosure. Must match SOP exactly.",
        }
        return contexts.get(section_type, "MODERATE — Standard body content.")
    
    def flatten(self) -> list[TreeNode]:
        """Get all leaf nodes"""
        return self.root.flatten()
    
    def serialize(self) -> dict:
        """Convert tree to JSON-serializable format"""
        def node_to_dict(node: TreeNode) -> dict:
            return {
                "type": node.section_type.value,
                "level": node.level,
                "heading": node.heading,
                "content": node.content[:200],  # Truncate for display
                "lines": f"{node.start_line}-{node.end_line}",
                "risk_context": node.risk_context,
                "children": [node_to_dict(child) for child in node.children],
            }
        
        return node_to_dict(self.root)
    
    def __repr__(self) -> str:
        """Pretty print tree"""
        def print_node(node: TreeNode, lines: list):
            indent = "  " * node.level
            section_str = f"[{node.section_type.value}]"
            heading_str = f" {node.heading}" if node.heading else ""
            content_preview = node.content[:60].replace('\n', ' ') if node.content else ""
            lines.append(f"{indent}{section_str}{heading_str}")
            if content_preview and node.section_type != SectionType.PARAGRAPH:
                lines.append(f"{indent}  → {content_preview}…")
            for child in node.children:
                print_node(child, lines)
        
        lines = []
        for child in self.root.children:
            print_node(child, lines)
        return '\n'.join(lines)
