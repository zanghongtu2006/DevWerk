import requests
from bs4 import BeautifulSoup
import time
import random


class T66ySpider:
    def __init__(self, base_url):
        self.base_url = base_url
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            'Referer': 'https://t66y.com/index.php'
        }
        self.results = []

    def get_page_content(self, url):
        """获取页面 HTML 内容"""
        try:
            # 网站通常使用 gbk 编码，需要手动指定防止乱码
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = response.apparent_encoding
            if response.status_code == 200:
                return response.text
        except Exception as e:
            print(f"请求失败: {url}, 错误: {e}")
        return None

    def parse_page(self, html):
        """解析每一楼的内容"""
        soup = BeautifulSoup(html, 'lxml')

        # 查找所有的回复楼层容器 (在该论坛中通常为 t_one 或 tr3 类型)
        posts = soup.find_all('tr', class_=['tr3', 'tr2'])

        for post in posts:
            author = post.find('b')
            content = post.find('div', class_='tpc_content')
            post_time = post.find('div', class_='tipad')  # 包含发布时间的部分

            if author and content:
                data = {
                    'author': author.get_text(strip=True),
                    'content': content.get_text(strip=True),
                    'time': post_time.get_text(strip=True) if post_time else "未知"
                }
                self.results.append(data)
                print(f"已爬取 [{data['author']}] 的内容")

    def run(self):
        # 1. 获取第一页并解析总页数
        first_page_html = self.get_page_content(self.base_url)
        if not first_page_html:
            return

        self.parse_page(first_page_html)

        # 2. 简单的翻页逻辑处理
        # 提示：该论坛翻页通常在 URL 后面拼接参数，或者从 html 中提取总页数
        # 这里演示手动设置页数或从页面提取
        # 注意：t66y 经常根据帖子 ID 进行路由，翻页 URL 规律通常是: index_2.html, index_3.html 等

        # 示例：假设我们爬取前 5 页（你可以根据 soup 自动提取总页数）
        for i in range(2, 6):
            # 构造下一页的 URL (根据实际观察到的 URL 规律修改)
            next_url = self.base_url.replace('.html', f'&page={i}')
            print(f"正在爬取第 {i} 页: {next_url}")

            html = self.get_page_content(next_url)
            if html:
                self.parse_page(html)
                # 随机延迟，避免被封 IP
                time.sleep(random.uniform(2, 5))
            else:
                break

        self.save_data()

    def save_data(self):
        """保存到本地文件"""
        with open('forum_content.txt', 'w', encoding='utf-8') as f:
            for item in self.results:
                f.write(f"作者: {item['author']}\n时间: {item['time']}\n内容: {item['content']}\n{'-' * 50}\n")
        print("数据抓取完成，已保存至 forum_content.txt")


if __name__ == "__main__":
    target_url = "https://t66y.com/htm_data/2602/20/7140948.html"
    spider = T66ySpider(target_url)
    spider.run()
