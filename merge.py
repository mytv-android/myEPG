import xml.etree.ElementTree as ET
from collections import defaultdict
import aiohttp
import asyncio
from tqdm.asyncio import tqdm_asyncio  # 引入 tqdm 的异步支持
from datetime import datetime
import gzip
import shutil
from xml.dom import minidom
import re
from opencc import OpenCC
import os
from tqdm import tqdm  # 引入 tqdm 的同步支持

def transform2_zh_hans(string):
    cc = OpenCC("t2s")
    new_str = cc.convert(string)
    return new_str

async def fetch_epg(url):
    connector = aiohttp.TCPConnector(limit=16, ssl=False)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
    }
    try:
        async with aiohttp.ClientSession(connector=connector, trust_env=True, headers=headers) as session:
            async with session.get(url) as response:
                return await response.text(encoding='utf-8')
    except aiohttp.ClientError as e:
        print(f"{url}HTTP请求错误: {e}")
    except asyncio.TimeoutError:
        print("{url}请求超时")
    except Exception as e:
        print(f"{url}其他错误: {e}")
    return None
        
def parse_epg(epg_content):
    try:
        parser = ET.XMLParser(encoding='UTF-8')
        root = ET.fromstring(epg_content, parser=parser)
    except ET.ParseError as e:
        print(f"Error parsing XML: {e}")
        print(f"Problematic content: {epg_content[:500]}")  
        return {}, defaultdict(list)

    channels = {}
    programmes = defaultdict(list)

    for channel in root.findall('channel'):
        channel_id = transform2_zh_hans(channel.get('id'))
        display_name = transform2_zh_hans(channel.find('display-name').text)
        channels[channel_id] = display_name

    for programme in root.findall('programme'):
        channel_id = transform2_zh_hans(programme.get('channel'))
        channel_start = datetime.strptime(
            re.sub(r'\s+', '', programme.get('start')), "%Y%m%d%H%M%S%z")
        channel_stop = datetime.strptime(
            re.sub(r'\s+', '', programme.get('stop')), "%Y%m%d%H%M%S%z")
        channel_title = transform2_zh_hans(programme.find('title').text)
        channel_elem = ET.SubElement(
            root, 'programme', attrib={"channel": channel_id, "start": channel_start.strftime("%Y%m%d%H%M%S +0800"), "stop": channel_stop.strftime("%Y%m%d%H%M%S +0800")})
        channel_elem_t = ET.SubElement(
            channel_elem, 'title')
        channel_elem_t.text = channel_title
        if programme.find('desc') is not None:
            channel_desc = transform2_zh_hans(programme.find('desc').text)
            channel_elem_desc = ET.SubElement(
                channel_elem, 'desc')
            channel_elem_desc.text = channel_desc
        programmes[channel_id].append(channel_elem)

    return channels, programmes

def write_to_xml(channels, programmes, filename):
    #目录不存在
    if not os.path.exists('output'):
        os.makedirs('output')
    current_time = datetime.now().strftime("%Y%m%d%H%M%S +0800")
    root = ET.Element('tv', attrib={'date': current_time})
    for channel_id in channels:
        channel_elem = ET.SubElement(root, 'channel', attrib={"id":channel_id})
        display_name_elem = ET.SubElement(channel_elem, 'display-name', attrib={"lang": "zh"})
        display_name_elem.text = channel_id
        for prog in programmes[channel_id]:
            prog.set('channel', channel_id)  # 设置 programme 的 channel 属性
            root.append(prog)

    # Beautify the XML output
    rough_string = ET.tostring(root, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(reparsed.toprettyxml(indent='\t', newl='\n'))


def compress_to_gz(input_filename, output_filename):
    with open(input_filename, 'rb') as f_in:
        with gzip.open(output_filename, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)


def get_urls():
    urls = []
    with open('config.txt', 'r', encoding='utf-8') as file:
        for line in file:
            line = line.strip()
            if line and not line.startswith('#'):
                urls.append(line)
    return urls

async def main():
    urls = get_urls()
    tasks = [fetch_epg(url) for url in urls]
    print("Fetching EPG data...")
    epg_contents = await tqdm_asyncio.gather(*tasks, desc="Fetching URLs")
    all_channels = set()
    all_channels_verify = set()
    all_programmes = defaultdict(list)
    print("Parsing EPG data...")
    with tqdm(total=len(epg_contents), desc="Parsing EPG", unit="file") as pbar:
        for epg_content in epg_contents:
            if epg_content is None:
                continue
            channels, programmes = parse_epg(epg_content)
            for channel_id, display_name in channels.items():
                display_name = display_name.replace(' ', '')
                if channel_id not in all_channels_verify and display_name not in all_channels_verify:
                    if not channel_id.isdigit():
                        all_channels_verify.add(channel_id)
                    all_channels.add(display_name)
                    all_channels_verify.add(display_name)
                    all_programmes[display_name] = programmes[channel_id]
            pbar.update(1)  # 更新进度条
    print("Writing to XML...")
    write_to_xml(all_channels, all_programmes, 'output/epg.xml')
    compress_to_gz('output/epg.xml', 'output/epg.gz')

if __name__ == '__main__':
    asyncio.run(main())
