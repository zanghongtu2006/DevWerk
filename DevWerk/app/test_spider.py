import requests
from bs4 import BeautifulSoup


def crawl_t66y_list(url):
    # 1. 设置请求头，模拟浏览器访问（非常重要）
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://t66y.com/'
    }

    try:
        # 2. 发送请求
        response = requests.get(url, headers=headers, timeout=10)

        # 3. 处理编码问题 (该站通常使用 GBK)
        # response.encoding = 'gbk'
        response.encoding = response.apparent_encoding
        if response.status_code == 200:
            # 4. 使用 BeautifulSoup 解析 HTML
            soup = BeautifulSoup(response.text, 'html.parser')

            # 5. 定位帖子列表
            # 观察源码发现，帖子通常在 id 为 "ajaxtable" 的 table 中，或者 class 为 "tr3 t_one" 的 tr 中
            # 这里选取所有包含帖子的 tr 标签
            threads = soup.find_all('tr', class_='tr3 t_one tac')

            print(f"{'标题':<50} | {'链接'}")
            print("-" * 80)

            for thread in threads:
                # 寻找标题所在的 <a> 标签，通常在 h3 标签内
                title_tag = thread.find('h3')
                if title_tag and title_tag.find('a'):
                    link_node = title_tag.find('a')
                    title = link_node.get_text(strip=True)
                    href = "https://t66y.com/" + link_node.get('href')

                    print(f"{title:<50} | {href}")
        else:
            print(f"请求失败，状态码: {response.status_code}")

    except Exception as e:
        print(f"发生错误: {e}")


import requests
from bs4 import BeautifulSoup


def crawl_t66y_post(post_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    try:
        response = requests.get(post_url, headers=headers, timeout=10)
        # 核心：使用 gb18030 解码解决“涓涓”乱码
        response.encoding = response.apparent_encoding

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')

            # 1. 抓取帖子标题 (通常在 h1 标签中)
            title = soup.find('h1', id='subject_tpc')
            print(f"【标题】: {title.get_text(strip=True) if title else '未找到标题'}")
            print("-" * 50)

            # 2. 抓取正文内容 (定位 class 为 tpc_content 的 div)
            main_content = soup.find('div', class_='tpc_content')

            if main_content:
                # 提取文本并保持基本换行
                # get_text(separator='\n') 会将 <br> 等标签转换为换行符
                text = main_content.get_text(separator='\n', strip=True)

                # 3. 如果需要抓取帖子里的图片链接
                images = main_content.find_all('img')
                img_urls = [img.get('ess-data') or img.get('src') for img in images]

                print("【正文内容】:")
                print(text)

                if img_urls:
                    print("\n【图片链接】:")
                    for url in img_urls:
                        print(url)
            else:
                print("未能定位到正文内容，请检查页面结构。")

        else:
            print(f"请求失败，状态码: {response.status_code}")

    except Exception as e:
        print(f"发生错误: {e}")


if __name__ == "__main__":
    # 填入你获取到的帖子链接
    test_url = "https://t66y.com/htm_data/2602/20/7140948.html"
    crawl_t66y_post(test_url)

# if __name__ == "__main__":
#     target_url = "https://t66y.com/thread0806.php?fid=20"
#     crawl_t66y_list(target_url)
#
