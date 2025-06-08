from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
import json
from datetime import datetime, timedelta, date
import random
import pdfplumber
# 在现有的导入语句中修改 werkzeug 的导入
from werkzeug.utils import secure_filename
from openai import OpenAI
import hashlib

app = Flask(__name__, template_folder='templates')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SECRET_KEY'] = 'your_secret_key_here'

# 确保上传目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 艾宾浩斯遗忘曲线复习间隔（天数）
REVIEW_INTERVALS = [0, 1, 3, 7, 15, 30, 60]  # 复习间隔：立即、1天、3天、7天、15天、30天、60天

# 初始化OpenAI客户端
def load_api_key():
    try:
        with open('api.txt', 'r', encoding='utf-8') as f:
            content = f.read()
            # 从api.txt中提取API密钥
            for line in content.split('\n'):
                if 'api_key' in line and '=' in line:
                    return line.split('=')[1].strip().strip('""')
    except:
        pass
    return "a7a67c86-2aa3-4d99-b97c-22201dbe53fb"  # 备用密钥

client = OpenAI(
    api_key=os.getenv("VOLC_API_KEY", load_api_key()),
    base_url="https://ark.cn-beijing.volces.com/api/v3"
)

# 全局变量存储单词数据和用户数据
word_data = []
users_data = {}  # 新增：用户数据
current_unit = 0
units_completed_today = 0

# 数据文件路径
DATA_FILE = 'word_data.json'
PROGRESS_FILE = 'user_progress.json'
USERS_FILE = 'users_data.json'  # 新增：用户数据文件
DONT_KNOW_FILE = 'dont_know_words.json'  # 新增："我不会"单词文件

# 新增：用户相关函数
def hash_password(password):
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(username, password):
    """创建新用户"""
    if username in users_data:
        return False, "用户名已存在"
    
    users_data[username] = {
        'password': hash_password(password),
        'created_date': datetime.now().isoformat(),
        'last_login': datetime.now().isoformat(),
        'check_in_dates': [],  # 签到日期列表
        'total_points': 0,  # 总积分
        'current_unit': 0,
        'units_completed_today': 0,
        'user_progress': {},
        'dont_know_words': []  # "我不会"的单词ID列表
    }
    save_users_data()
    return True, "注册成功"

def verify_user(username, password):
    """验证用户登录"""
    if username not in users_data:
        return False, "用户不存在"
    
    if users_data[username]['password'] != hash_password(password):
        return False, "密码错误"
    
    # 更新最后登录时间
    users_data[username]['last_login'] = datetime.now().isoformat()
    save_users_data()
    return True, "登录成功"

def get_current_user():
    """获取当前登录用户"""
    return session.get('username')

def is_user_logged_in():
    """检查用户是否已登录"""
    return 'username' in session

def daily_check_in(username):
    """每日签到"""
    today = date.today().isoformat()
    user = users_data[username]
    
    if today not in user['check_in_dates']:
        user['check_in_dates'].append(today)
        user['total_points'] += 10  # 签到获得10积分
        save_users_data()
        return True, f"签到成功！获得10积分，当前积分：{user['total_points']}"
    else:
        return False, "今天已经签到过了"

def add_dont_know_word(username, word_id):
    """添加"我不会"的单词"""
    user = users_data[username]
    if word_id not in user['dont_know_words']:
        user['dont_know_words'].append(word_id)
        # 将该单词的复习时间设置为立即
        if word_id < len(word_data):
            word_data[word_id]['next_review'] = datetime.now()
            word_data[word_id]['review_level'] = 0  # 重置复习等级
        save_users_data()
        save_data()
        return True, "已标记为不会的单词，将加强复习"
    return False, "该单词已在不会列表中"

def get_user_stats(username):
    """获取用户统计信息"""
    user = users_data[username]
    today = date.today().isoformat()
    
    return {
        'total_points': user['total_points'],
        'check_in_today': today in user['check_in_dates'],
        'consecutive_days': get_consecutive_check_in_days(username),
        'dont_know_count': len(user['dont_know_words']),
        'current_unit': user['current_unit'] + 1,
        'units_completed_today': user['units_completed_today']
    }

def get_consecutive_check_in_days(username):
    """获取连续签到天数"""
    user = users_data[username]
    check_in_dates = sorted(user['check_in_dates'], reverse=True)
    
    if not check_in_dates:
        return 0
    
    consecutive = 0
    current_date = date.today()
    
    for check_date_str in check_in_dates:
        check_date = date.fromisoformat(check_date_str)
        if check_date == current_date:
            consecutive += 1
            current_date -= timedelta(days=1)
        else:
            break
    
    return consecutive

def save_users_data():
    """保存用户数据"""
    try:
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(users_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存用户数据失败: {e}")

def load_users_data():
    """加载用户数据"""
    global users_data
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                users_data = json.load(f)
                print(f"加载了{len(users_data)}个用户")
    except Exception as e:
        print(f"加载用户数据失败: {e}")
        users_data = {}

# 修改现有的save_data和load_data函数以支持多用户
def save_data():
    """保存单词数据和用户进度到文件"""
    try:
        # 保存单词数据
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            data_to_save = []
            for word in word_data:
                word_copy = word.copy()
                if 'next_review' in word_copy and isinstance(word_copy['next_review'], datetime):
                    word_copy['next_review'] = word_copy['next_review'].isoformat()
                data_to_save.append(word_copy)
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        
        # 如果用户已登录，保存到用户数据中
        username = get_current_user()
        if username and username in users_data:
            users_data[username]['current_unit'] = current_unit
            users_data[username]['units_completed_today'] = units_completed_today
            save_users_data()
    except Exception as e:
        print(f"保存数据失败: {e}")

def load_data():
    """从文件加载单词数据和用户进度"""
    global word_data, current_unit, units_completed_today
    
    try:
        # 加载单词数据
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
                for word in loaded_data:
                    if 'next_review' in word and isinstance(word['next_review'], str):
                        word['next_review'] = datetime.fromisoformat(word['next_review'])
                word_data = loaded_data
                print(f"加载了{len(word_data)}个单词")
        
        # 只在请求上下文中获取用户信息
        try:
            username = get_current_user()
            if username and username in users_data:
                user = users_data[username]
                current_unit = user.get('current_unit', 0)
                units_completed_today = user.get('units_completed_today', 0)
                print(f"加载用户进度: 当前单元{current_unit + 1}, 今日完成{units_completed_today}个单元")
        except RuntimeError:
            # 在应用启动时没有请求上下文，这是正常的
            pass
        
        # 如果没有单词数据且存在默认PDF，加载它
        if not word_data and os.path.exists('english.pdf'):
            word_data = extract_words_from_pdf('english.pdf')
            print(f"从默认PDF加载了{len(word_data)}个单词")
    except Exception as e:
        print(f"加载数据失败: {e}")

# 新增：登录注册路由
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.json
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': '用户名和密码不能为空'})
        
        success, message = verify_user(username, password)
        if success:
            session['username'] = username
            load_data()  # 重新加载用户数据
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'message': message})
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        data = request.json
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': '用户名和密码不能为空'})
        
        if len(username) < 3:
            return jsonify({'success': False, 'message': '用户名至少3个字符'})
        
        if len(password) < 6:
            return jsonify({'success': False, 'message': '密码至少6个字符'})
        
        success, message = create_user(username, password)
        if success:
            session['username'] = username
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'message': message})
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# 新增：签到路由
@app.route('/api/check_in', methods=['POST'])
def check_in():
    username = get_current_user()
    if not username:
        return jsonify({'success': False, 'message': '请先登录'})
    
    success, message = daily_check_in(username)
    return jsonify({'success': success, 'message': message})

# 新增："我不会"功能路由
@app.route('/api/dont_know', methods=['POST'])
def dont_know():
    username = get_current_user()
    if not username:
        return jsonify({'success': False, 'message': '请先登录'})
    
    data = request.json
    word_id = data.get('word_id')
    
    if word_id is None:
        return jsonify({'success': False, 'message': '无效的单词ID'})
    
    success, message = add_dont_know_word(username, word_id)
    return jsonify({'success': success, 'message': message})

# 修改现有路由以支持用户登录检查
@app.route('/')
def index():
    if not is_user_logged_in():
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/practice')
def practice():
    if not is_user_logged_in():
        return redirect(url_for('login'))
    return render_template('practice.html')

@app.route('/vocabulary')
def vocabulary():
    """词库管理页面"""
    if not is_user_logged_in():
        return redirect('/login')
    return render_template('vocabulary.html')

# 修改统计API以包含用户信息
@app.route('/api/stats')
def get_stats():
    username = get_current_user()
    if not username:
        return jsonify({'error': '请先登录'})
    
    total_words = len(word_data)
    learned_words = len([w for w in word_data if w['review_level'] > 0])
    review_words = len(get_words_for_review())
    user_stats = get_user_stats(username)
    
    return jsonify({
        'total_words': total_words,
        'learned_words': learned_words,
        'review_words': review_words,
        'units_completed_today': units_completed_today,
        'current_unit': current_unit + 1,
        'user_stats': user_stats
    })

# 添加上传路由
@app.route('/upload', methods=['POST'])
def upload_file():
    """处理文件上传"""
    username = get_current_user()
    if not username:
        return jsonify({'success': False, 'message': '请先登录'})
    
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有选择文件'})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '没有选择文件'})
    
    # 创建用户专属上传目录
    user_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], f'user_{username}')
    os.makedirs(user_upload_dir, exist_ok=True)
    
    try:
        # 保存文件
        filename = secure_filename(file.filename)
        file_path = os.path.join(user_upload_dir, filename)
        file.save(file_path)
        
        # 根据文件类型处理
        new_words = []
        if filename.lower().endswith('.pdf'):
            new_words = extract_words_from_pdf(file_path)
        elif filename.lower().endswith('.txt'):
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            new_words = parse_text_with_ai_enhanced(content)
        else:
            return jsonify({'success': False, 'message': '不支持的文件格式，请上传PDF或TXT文件'})
        
        if new_words:
            # 添加新单词到全局单词数据
            global word_data
            existing_words = {w['english'].lower() for w in word_data}
            added_count = 0
            
            for word in new_words:
                if word['english'].lower() not in existing_words:
                    word_data.append(word)
                    existing_words.add(word['english'].lower())
                    added_count += 1
            
            # 保存数据
            save_data()
            
            return jsonify({
                'success': True, 
                'message': f'成功上传并解析了 {added_count} 个新单词',
                'total_words': len(word_data)
            })
        else:
            return jsonify({'success': False, 'message': '文件中没有找到有效的单词'})
            
    except Exception as e:
        error_msg = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        print(f"上传文件处理错误: {error_msg}")
        return jsonify({'success': False, 'message': f'文件处理失败: {error_msg}'})

def get_words_for_review():
    """获取需要复习的单词"""
    now = datetime.now()
    review_words = []
    
    for word in word_data:
        # 检查是否需要复习
        if 'next_review' in word and word['next_review']:
            if isinstance(word['next_review'], str):
                next_review = datetime.fromisoformat(word['next_review'])
            else:
                next_review = word['next_review']
            
            if next_review <= now:
                review_words.append(word)
        elif word.get('review_level', 0) == 0:  # 新单词也需要学习
            review_words.append(word)
    
    return review_words

def extract_words_from_pdf(file_path):
    """从PDF文件中提取单词"""
    words = []
    try:
        with pdfplumber.open(file_path) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() or ""
        
        # 使用增强的AI解析文本（包含详细信息）
        words = parse_text_with_ai_enhanced(text)
    except Exception as e:
        print(f"PDF解析错误: {e}".encode('utf-8', errors='ignore').decode('utf-8'))
    
    return words

# 在parse_text_with_ai函数之后添加新的AI功能函数
def generate_word_details_with_ai(english_word, chinese_meaning):
    """使用AI生成单词的详细信息：例句、词性、音标"""
    try:
        response = client.chat.completions.create(
            model="ep-20250216123731-lwrsq",
            messages=[
                {
                    "role": "system",
                    "content": "你是一个英语词典助手。请为给定的英语单词生成详细信息，包括音标、词性、英文例句和中文翻译。返回JSON格式，包含字段：phonetic（音标）、part_of_speech（词性）、example_sentence（英文例句）、example_translation（例句中文翻译）。".encode('utf-8').decode('utf-8')
                },
                {
                    "role": "user",
                    "content": f"请为英语单词'{english_word}'（中文意思：{chinese_meaning}）生成详细信息。".encode('utf-8').decode('utf-8')
                }
            ],
            temperature=0.3
        )
        
        result = response.choices[0].message.content
        # 尝试解析JSON
        import re
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if json_match:
            details = json.loads(json_match.group())
            return {
                'phonetic': details.get('phonetic', ''),
                'part_of_speech': details.get('part_of_speech', ''),
                'example_sentence': details.get('example_sentence', ''),
                'example_translation': details.get('example_translation', '')
            }
    except Exception as e:
        print(f"AI生成单词详情错误: {e}".encode('utf-8', errors='ignore').decode('utf-8'))
    
    return {
        'phonetic': '',
        'part_of_speech': '',
        'example_sentence': '',
        'example_translation': ''
    }

# 修改parse_text_with_ai函数以包含详细信息
def parse_text_with_ai_enhanced(text):
    """使用AI解析文本中的单词并生成详细信息"""
    words = []
    try:
        print(f"开始解析文本: {text[:200]}...".encode('utf-8', errors='ignore').decode('utf-8'))
        
        response = client.chat.completions.create(
            model="ep-20250216123731-lwrsq",
            messages=[
                {
                    "role": "system",
                    "content": "你是一个英语单词提取助手。请从给定的文本中提取英语单词，如果文本中有中文释义则一起提取，如果只有英文单词则为其生成合适的中文翻译。返回JSON格式的数组，每个单词对象包含'english'和'chinese'字段。只提取有意义的单词，忽略语法词汇和数字序号。".encode('utf-8').decode('utf-8')
                },
                {
                    "role": "user",
                    "content": f"请从以下文本中提取英语单词，如果有中文释义则一起提取，如果只有英文单词则为其生成中文翻译：\n\n{text[:2000]}".encode('utf-8').decode('utf-8')  # 限制文本长度并确保UTF-8编码
                }
            ],
            temperature=0.3
        )
        
        result = response.choices[0].message.content
        print(f"AI返回结果: {result}".encode('utf-8', errors='ignore').decode('utf-8'))
        
        # 尝试解析JSON
        import re
        json_match = re.search(r'\[.*\]', result, re.DOTALL)
        if json_match:
            words_data = json.loads(json_match.group())
            print(f"解析到 {len(words_data)} 个单词".encode('utf-8', errors='ignore').decode('utf-8'))
            for word_info in words_data:
                if isinstance(word_info, dict) and 'english' in word_info and 'chinese' in word_info:
                    # 简化版本：不调用AI生成详细信息，使用默认值
                    words.append({
                        'english': word_info['english'],
                        'chinese': word_info['chinese'],
                        'phonetic': '',
                        'part_of_speech': '',
                        'example_sentence': '',
                        'example_translation': '',
                        'review_level': 0,
                        'next_review': datetime.now(),
                        'error_count': 0
                    })
                    print(f"添加单词: {word_info['english']} - {word_info['chinese']}".encode('utf-8', errors='ignore').decode('utf-8'))
        else:
            print("未找到有效的JSON格式".encode('utf-8', errors='ignore').decode('utf-8'))
    except Exception as e:
        print(f"AI解析错误: {e}".encode('utf-8', errors='ignore').decode('utf-8'))
        import traceback
        print(f"详细错误信息: {traceback.format_exc()}".encode('utf-8', errors='ignore').decode('utf-8'))
    
    print(f"最终返回 {len(words)} 个单词".encode('utf-8', errors='ignore').decode('utf-8'))
    return words

def get_current_unit_words():
    """获取当前单元的单词（20个）"""
    start_idx = current_unit * 20
    end_idx = min(start_idx + 20, len(word_data))
    return word_data[start_idx:end_idx]

def generate_chinese_options(correct_chinese):
    """生成中文选择题选项"""
    options = [correct_chinese]
    
    # 从其他单词中随机选择3个作为干扰项
    other_words = [w for w in word_data if w['chinese'] != correct_chinese]
    random_words = random.sample(other_words, min(3, len(other_words)))
    
    for word in random_words:
        options.append(word['chinese'])
    
    # 如果选项不够4个，添加一些常见的中文词汇
    while len(options) < 4:
        dummy_options = ['苹果', '香蕉', '桌子', '椅子', '汽车', '房子', '学校', '医院']
        for dummy in dummy_options:
            if dummy not in options:
                options.append(dummy)
                break
    
    random.shuffle(options)
    return options

@app.route('/api/get_question')
def get_question():
    """获取练习题目"""
    # 从URL参数获取模式类型
    mode = request.args.get('mode', 'mixed')  # mixed, chinese_to_english, english_to_chinese
    
    # 优先获取需要复习的单词
    review_words = get_words_for_review()
    
    if review_words:
        word = random.choice(review_words)
    else:
        # 如果没有需要复习的单词，从当前单元获取
        unit_words = get_current_unit_words()
        if not unit_words:
            return jsonify({'message': '没有更多单词了'})
        word = random.choice(unit_words)
    
    if mode == 'chinese_to_english':
        # 模式1：显示中文，用户输入英文
        return jsonify({
            'type': 'input',
            'mode': 'chinese_to_english',
            'question': word['chinese'],
            'correct_answer': word['english'],
            'word_id': word_data.index(word),
            'placeholder': '请输入英文单词'
        })
    elif mode == 'english_to_chinese':
        # 模式2：显示英文，提供4个中文选项
        options = generate_chinese_options(word['chinese'])
        return jsonify({
            'type': 'multiple_choice',
            'mode': 'english_to_chinese',
            'question': word['english'],
            'options': options,
            'correct_answer': word['chinese'],
            'word_id': word_data.index(word)
        })
    else:
        # 混合模式：随机选择
        question_type = random.choice(['english_to_chinese', 'chinese_to_english'])
        
        if question_type == 'english_to_chinese':
            options = generate_chinese_options(word['chinese'])
            return jsonify({
                'type': 'multiple_choice',
                'mode': 'english_to_chinese',
                'question': word['english'],
                'options': options,
                'correct_answer': word['chinese'],
                'word_id': word_data.index(word)
            })
        else:
            return jsonify({
                'type': 'input',
                'mode': 'chinese_to_english',
                'question': word['chinese'],
                'correct_answer': word['english'],
                'word_id': word_data.index(word),
                'placeholder': '请输入英文单词'
            })

@app.route('/api/submit_answer', methods=['POST'])
def submit_answer():
    """提交答案"""
    global units_completed_today
    data = request.json
    word_id = data['word_id']
    user_answer = data['answer'].strip().lower()
    correct_answer = data['correct_answer'].strip().lower()
    
    is_correct = user_answer == correct_answer
    word = word_data[word_id]
    
    if is_correct:
        # 答对了，提升复习等级
        word['review_level'] = min(word['review_level'] + 1, len(REVIEW_INTERVALS) - 1)
        interval_days = REVIEW_INTERVALS[word['review_level']]
        word['next_review'] = datetime.now() + timedelta(days=interval_days)
        word['error_count'] = 0
    else:
        # 答错了，增加错误次数，重置复习等级
        word['error_count'] += 1
        word['review_level'] = 0
        word['next_review'] = datetime.now() + timedelta(minutes=5)  # 5分钟后重新复习
    
    # 检查是否完成单元
    unit_progress = check_unit_completion()
    
    # 保存数据
    save_data()
    
    return jsonify({
        'correct': is_correct,
        'correct_answer': data['correct_answer'],
        'unit_progress': unit_progress
    })

def check_unit_completion():
    """检查单元完成情况"""
    global current_unit, units_completed_today
    
    unit_words = get_current_unit_words()
    completed_words = 0
    
    for word in unit_words:
        # 修改：只要答过题就算练习过（无论对错）
        if word['review_level'] > 0 or word['error_count'] > 0:
            completed_words += 1
    
    if completed_words >= len(unit_words):  # 单元完成
        old_unit = current_unit + 1  # 显示完成的单元号（从1开始）
        current_unit += 1
        units_completed_today += 1
        return {
            'unit_completed': True,
            'unit_number': old_unit,
            'total_units': current_unit,
            'progress': f"{len(unit_words)}/{len(unit_words)}"  # 完成时显示满进度
        }
    
    return {
        'unit_completed': False,
        'progress': f"{completed_words}/{len(unit_words)}"
    }

if __name__ == '__main__':
    # 加载保存的数据
    load_users_data()
    load_data()
    
    app.run(host='0.0.0.0', port=5000, debug=True)

# 添加词库管理API路由
@app.route('/api/words', methods=['GET'])
def get_words():
    """获取词库列表"""
    if not is_user_logged_in():
        return jsonify({'success': False, 'message': '请先登录'})
    
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    search = request.args.get('search', '')
    
    # 过滤单词
    filtered_words = word_data
    if search:
        filtered_words = [w for w in word_data if search.lower() in w['english'].lower() or search in w['chinese']]
    
    # 分页
    start = (page - 1) * per_page
    end = start + per_page
    words_page = filtered_words[start:end]
    
    # 添加索引信息
    for i, word in enumerate(words_page):
        word['id'] = start + i
    
    return jsonify({
        'success': True,
        'words': words_page,
        'total': len(filtered_words),
        'page': page,
        'per_page': per_page,
        'total_pages': (len(filtered_words) + per_page - 1) // per_page
    })

@app.route('/api/words/<int:word_id>', methods=['PUT'])
def edit_word(word_id):
    """编辑单词"""
    if not is_user_logged_in():
        return jsonify({'success': False, 'message': '请先登录'})
    
    if word_id >= len(word_data):
        return jsonify({'success': False, 'message': '单词不存在'})
    
    data = request.get_json()
    
    # 更新单词信息
    word_data[word_id].update({
        'english': data.get('english', word_data[word_id]['english']),
        'chinese': data.get('chinese', word_data[word_id]['chinese']),
        'phonetic': data.get('phonetic', word_data[word_id].get('phonetic', '')),
        'part_of_speech': data.get('part_of_speech', word_data[word_id].get('part_of_speech', '')),
        'example_sentence': data.get('example_sentence', word_data[word_id].get('example_sentence', '')),
        'example_translation': data.get('example_translation', word_data[word_id].get('example_translation', ''))
    })
    
    save_data()
    return jsonify({'success': True, 'message': '单词更新成功'})

@app.route('/api/words/<int:word_id>', methods=['DELETE'])
def delete_word(word_id):
    """删除单词"""
    if not is_user_logged_in():
        return jsonify({'success': False, 'message': '请先登录'})
    
    if word_id >= len(word_data):
        return jsonify({'success': False, 'message': '单词不存在'})
    
    # 删除单词
    deleted_word = word_data.pop(word_id)
    
    # 更新所有用户的"我不会"列表中的索引
    for username in users_data:
        user = users_data[username]
        # 移除被删除的单词ID
        if word_id in user['dont_know_words']:
            user['dont_know_words'].remove(word_id)
        # 更新大于被删除ID的索引
        user['dont_know_words'] = [idx - 1 if idx > word_id else idx for idx in user['dont_know_words']]
    
    save_data()
    save_users_data()
    return jsonify({'success': True, 'message': f'单词"{deleted_word["english"]}"删除成功'})

@app.route('/api/words', methods=['POST'])
def add_word():
    """添加新单词"""
    if not is_user_logged_in():
        return jsonify({'success': False, 'message': '请先登录'})
    
    data = request.get_json()
    english = data.get('english', '').strip()
    chinese = data.get('chinese', '').strip()
    
    if not english or not chinese:
        return jsonify({'success': False, 'message': '英文单词和中文释义不能为空'})
    
    # 检查单词是否已存在
    for word in word_data:
        if word['english'].lower() == english.lower():
            return jsonify({'success': False, 'message': '该单词已存在'})
    
    # 使用AI生成详细信息
    auto_generate = data.get('auto_generate', True)
    if auto_generate:
        details = generate_word_details_with_ai(english, chinese)
    else:
        details = {
            'phonetic': data.get('phonetic', ''),
            'part_of_speech': data.get('part_of_speech', ''),
            'example_sentence': data.get('example_sentence', ''),
            'example_translation': data.get('example_translation', '')
        }
    
    # 添加新单词
    new_word = {
        'english': english,
        'chinese': chinese,
        'phonetic': details['phonetic'],
        'part_of_speech': details['part_of_speech'],
        'example_sentence': details['example_sentence'],
        'example_translation': details['example_translation'],
        'review_level': 0,
        'next_review': datetime.now(),
        'error_count': 0
    }
    
    word_data.append(new_word)
    save_data()
    
    return jsonify({
        'success': True, 
        'message': '单词添加成功',
        'word': new_word
    })

@app.route('/api/words/batch', methods=['POST'])
def batch_add_words():
    """批量添加单词"""
    if not is_user_logged_in():
        return jsonify({'success': False, 'message': '请先登录'})
    
    data = request.get_json()
    words_list = data.get('words', [])
    auto_generate = data.get('auto_generate', True)
    
    added_count = 0
    skipped_count = 0
    
    for word_info in words_list:
        english = word_info.get('english', '').strip()
        chinese = word_info.get('chinese', '').strip()
        
        if not english or not chinese:
            skipped_count += 1
            continue
        
        # 检查单词是否已存在
        exists = any(word['english'].lower() == english.lower() for word in word_data)
        if exists:
            skipped_count += 1
            continue
        
        # 使用AI生成详细信息
        if auto_generate:
            details = generate_word_details_with_ai(english, chinese)
        else:
            details = {
                'phonetic': word_info.get('phonetic', ''),
                'part_of_speech': word_info.get('part_of_speech', ''),
                'example_sentence': word_info.get('example_sentence', ''),
                'example_translation': word_info.get('example_translation', '')
            }
        
        # 添加新单词
        new_word = {
            'english': english,
            'chinese': chinese,
            'phonetic': details['phonetic'],
            'part_of_speech': details['part_of_speech'],
            'example_sentence': details['example_sentence'],
            'example_translation': details['example_translation'],
            'review_level': 0,
            'next_review': datetime.now(),
            'error_count': 0
        }
        
        word_data.append(new_word)
        added_count += 1
    
    save_data()
    
    return jsonify({
        'success': True,
        'message': f'批量添加完成：成功添加{added_count}个单词，跳过{skipped_count}个单词',
        'added_count': added_count,
        'skipped_count': skipped_count
    })

@app.route('/api/words/<int:word_id>/generate-details', methods=['POST'])
def generate_word_details(word_id):
    """为指定单词生成AI详细信息"""
    if not is_user_logged_in():
        return jsonify({'success': False, 'message': '请先登录'})
    
    if word_id >= len(word_data):
        return jsonify({'success': False, 'message': '单词不存在'})
    
    word = word_data[word_id]
    details = generate_word_details_with_ai(word['english'], word['chinese'])
    
    # 更新单词信息
    word.update(details)
    save_data()
    
    return jsonify({
        'success': True,
        'message': '单词详细信息生成成功',
        'details': details
    })