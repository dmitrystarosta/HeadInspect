from html.parser import HTMLParser

class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title=False; self.title_parts=[]; self.meta_description=None; self.og={}
    def handle_starttag(self, tag, attrs):
        tag=tag.lower()
        if tag=="title":
            self.in_title=True; return
        if tag!="meta": return
        d={k.lower():(v or "") for k,v in attrs}; content=d.get("content","").strip()
        if d.get("name","").strip().lower()=="description" and content and self.meta_description is None:
            self.meta_description=content
        prop=d.get("property","").strip().lower()
        if prop.startswith("og:") and content: self.og.setdefault(prop,[]).append(content)
    def handle_endtag(self, tag):
        if tag.lower()=="title": self.in_title=False
    def handle_data(self, data):
        if self.in_title: self.title_parts.append(data)
    @property
    def title(self):
        v=" ".join(x.strip() for x in self.title_parts if x.strip()).strip()
        return v or None
