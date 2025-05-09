from flask import Flask
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=20) # 您可以根据需要调整 max_workers
