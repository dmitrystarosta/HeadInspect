from ..models import OpenGraphData

def analyze_open_graph(og):
    first=lambda k: og.get(k,[None])[0] if og.get(k) else None
    images=og.get("og:image",[])+og.get("og:image:url",[])
    images=list(dict.fromkeys(images))
    data=OpenGraphData(
        title=first("og:title"), description=first("og:description"), url=first("og:url"),
        type=first("og:type"), image=images[0] if images else first("og:image:secure_url"),
        image_width=first("og:image:width"), image_height=first("og:image:height"), image_count=len(images)
    )
    errors=[]; warnings=[]
    if not data.title: errors.append("Нет og:title")
    if not data.image: errors.append("Нет og:image")
    if not data.description: warnings.append("Нет og:description")
    if not data.url: warnings.append("Нет og:url")
    if not data.type: warnings.append("Нет og:type")
    if data.image_count>1: warnings.append(f"Найдено несколько og:image: {data.image_count}")
    return data, errors, warnings
