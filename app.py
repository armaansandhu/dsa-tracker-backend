from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from functools import wraps
import bcrypt
import jwt
import pymysql
from pymysql.cursors import DictCursor


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuration
JWT_SECRET = os.environ.get('JWT_SECRET', secrets.token_hex(32))
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_HOURS = 24 * 7  # 7 days

MYSQL_URL = os.environ.get('MYSQL_URL') or os.environ.get('DATABASE_URL')


if MYSQL_URL:
    # Parse URL format: mysql://user:password@host:port/database
    from urllib.parse import urlparse
    parsed = urlparse(MYSQL_URL)
    MYSQL_HOST = parsed.hostname
    MYSQL_PORT = parsed.port or 3306
    MYSQL_USER = parsed.username
    MYSQL_PASSWORD = parsed.password
    MYSQL_DATABASE = parsed.path.lstrip('/')
else:
    # Fallback to individual environment variables
    MYSQL_HOST = os.environ.get('MYSQLHOST', 'localhost')
    MYSQL_PORT = int(os.environ.get('MYSQLPORT', 3306))
    MYSQL_USER = os.environ.get('MYSQLUSER', 'root')
    MYSQL_PASSWORD = os.environ.get('MYSQLPASSWORD', '')
    MYSQL_DATABASE = os.environ.get('MYSQLDATABASE', 'railway')
# ============= DATABASE =============

@contextmanager
def get_db():
    """Get MySQL database connection"""
    conn = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset='utf8mb4',
        cursorclass=DictCursor,
        autocommit=False
    )
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize database tables"""
    with get_db() as conn:
        cursor = conn.cursor()

        # Create the users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                username VARCHAR(100),
                is_admin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP NULL,
                INDEX idx_email (email),
                INDEX idx_username (username)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        ''')

        # Create questions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS questions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(500) UNIQUE NOT NULL,
                acceptance_rate DECIMAL(5,2),
                average_frequency DECIMAL(5,2),
                topics JSON,
                link VARCHAR(500),
                difficulty ENUM('EASY', 'MEDIUM', 'HARD') NOT NULL,
                companies JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_name (name),
                INDEX idx_difficulty (difficulty)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        ''')
        
        # Create progress table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS progress (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                question_id INT NOT NULL,
                status ENUM('unattempted', 'solved', 'practice') DEFAULT 'unattempted',
                best_time INT,
                attempt_count INT DEFAULT 0,
                date_completed TIMESTAMP NULL,
                notes TEXT,
                last_attempted TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY unique_user_question (user_id, question_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
                INDEX idx_user_status (user_id, status),
                INDEX idx_user_question (user_id, question_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        ''')

        conn.commit()
        print("Database tables initialized successfully")

def populate_initial_data():
    """Populate database with initial data if empty"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM questions')
        count = cursor.fetchone()['count']

        if count == 0:
            print("Database is empty. Loading initial data...")
            # Check if initial data file exists
            data_file = os.path.join(os.path.dirname(__file__), 'initial_data.json')
            if os.path.exists(data_file):
                try:
                    with open(data_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                        # Handle both list and dict formats
                        if isinstance(data, list):
                            questions = data
                        elif isinstance(data, dict):
                            questions = data.get('questions', [])
                        else:
                            print(f"Unexpected data format: {type(data)}")
                            return

                        imported = 0
                        for q in questions:
                            try:
                                print(f"Inserting question : {q.get("name")}")
                                topics = [t.lower() for t in q.get('topics', [])]
                                cursor.execute('''
                                    INSERT IGNORE INTO questions 
                                    (name, acceptance_rate, average_frequency, topics, link, difficulty, companies)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                                ''', (
                                    q.get('name'),
                                    q.get('acceptanceRate'),
                                    q.get('averageFrequency'),
                                    json.dumps(topics),
                                    q.get('link'),
                                    q.get('difficulty'),
                                    json.dumps(q.get('companies', []))
                                ))
                                if cursor.rowcount > 0:
                                    imported += 1
                            except Exception as e:
                                print(f"Error importing question '{q.get('name', 'unknown')}': {e}")

                        conn.commit()
                        print(f"✓ Successfully loaded {imported} questions from initial_data.json")
                except json.JSONDecodeError as e:
                    print(f"Error parsing JSON file: {e}")
                except Exception as e:
                    print(f"Error loading initial data: {e}")
            else:
                print("⚠ No initial_data.json found. Use /api/import to add questions.")
        else:
            print(f"✓ Database already contains {count} questions. Skipping initial data load.")

# ============= AUTHENTICATION HELPERS =============

def hash_password(password):
    """Hash password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password, password_hash):
    """Verify password against hash"""
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))


def generate_token(user_id):
    """Generate JWT token"""
    payload = {
        'user_id': user_id,
        'exp': datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS),
        'iat': datetime.now(timezone.utc)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token):
    """Decode JWT token"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload['user_id']
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def token_required(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]

        if not token:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401

        user_id = decode_token(token)
        if user_id is None:
            return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401

        return f(*args, user_id=user_id, **kwargs)

    return decorated


# ============= HELPER FUNCTIONS =============

def serialize_question(question, progress, show_all_companies=False, filter_company=None):
    """Serialize question with user progress"""
    # FIX: Parse JSON fields if they're strings
    companies = question['companies'] if question['companies'] else []
    if isinstance(companies, str):
        companies = json.loads(companies)
    
    topics = question['topics'] if question['topics'] else []
    if isinstance(topics, str):
        topics = json.loads(topics)

    if not show_all_companies and filter_company:
        companies = [c for c in companies if c['name'].lower() == filter_company.lower()]

    return {
        'id': question['id'],
        'name': question['name'],
        'acceptanceRate': float(question['acceptance_rate']) if question['acceptance_rate'] else None,
        'averageFrequency': float(question['average_frequency']) if question['average_frequency'] else None,
        'topics': topics,
        'link': question['link'],
        'difficulty': question['difficulty'],
        'companies': companies,
        'status': progress['status'] if progress else 'unattempted',
        'bestTime': progress['best_time'] if progress else None,
        'attemptCount': progress['attempt_count'] if progress else 0,
        'dateCompleted': progress['date_completed'].isoformat() if progress and progress['date_completed'] else None,
        'notes': progress['notes'] if progress else None
    }


def format_time(seconds):
    """Convert seconds to readable time format"""
    if seconds is None:
        return None
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


# ============= AUTHENTICATION ENDPOINTS =============

@app.route('/api/auth/register', methods=['POST'])
def register():
    """Register new user"""
    data = request.json
    email = data.get('email')
    password = data.get('password')
    username = data.get('username')

    if not email or not password:
        return jsonify({'success': False, 'error': 'Email and password required'}), 400

    if len(password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400

    with get_db() as conn:
        cursor = conn.cursor()

        # Check if email already exists
        cursor.execute('SELECT id FROM users WHERE email = %s', (email,))
        if cursor.fetchone():
            return jsonify({'success': False, 'error': 'Email already registered'}), 400

        # Check if username already exists
        if username:
            cursor.execute('SELECT id FROM users WHERE username = %s', (username,))
            if cursor.fetchone():
                return jsonify({'success': False, 'error': 'Username already taken'}), 400

        # Create user
        password_hash = hash_password(password)
        cursor.execute('''
            INSERT INTO users (email, password_hash, username)
            VALUES (%s, %s, %s)
        ''', (email, password_hash, username))

        user_id = cursor.lastrowid
        conn.commit()

        # Generate token
        token = generate_token(user_id)

        return jsonify({
            'success': True,
            'message': 'User registered successfully',
            'token': token,
            'user': {
                'id': user_id,
                'email': email,
                'username': username,
                'is_admin': False
            }
        }), 201


@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login user"""
    data = request.json
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({'success': False, 'error': 'Email and password required'}), 400

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM users WHERE email = %s', (email,))
        user = cursor.fetchone()

        if not user or not verify_password(password, user['password_hash']):
            return jsonify({'success': False, 'error': 'Invalid email or password'}), 401

        # Update last login
        cursor.execute('UPDATE users SET last_login = %s WHERE id = %s',
                       (datetime.now(timezone.utc), user['id']))
        conn.commit()

        # Generate token
        token = generate_token(user['id'])

        return jsonify({
            'success': True,
            'token': token,
            'user': {
                'id': user['id'],
                'email': user['email'],
                'username': user['username'],
                'is_admin': user['is_admin']
            }
        })


def admin_required(f):
    """Decorator to require admin authentication"""

    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]

        if not token:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401

        user_id = decode_token(token)
        if user_id is None:
            return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401

        # Check if user is admin
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT is_admin FROM users WHERE id = %s', (user_id,))
            user = cursor.fetchone()

            if not user or not user['is_admin']:
                return jsonify({'success': False, 'error': 'Admin access required'}), 403

        return f(*args, user_id=user_id, **kwargs)

    return decorated



@app.route('/api/auth/me', methods=['GET'])
@token_required
def get_current_user(user_id):
    """Get current user info"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, email, username, is_admin, created_at, last_login FROM users WHERE id = %s',
                       (user_id,))
        user = cursor.fetchone()

        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        # Convert datetime objects to ISO format strings
        user_data = dict(user)
        if user_data.get('created_at'):
            user_data['created_at'] = user_data['created_at'].isoformat()
        if user_data.get('last_login'):
            user_data['last_login'] = user_data['last_login'].isoformat()

        return jsonify({
            'success': True,
            'user': user_data
        })


# ============= QUESTIONS ENDPOINTS =============

@app.route('/api/questions', methods=['GET'])
@token_required
def get_questions(user_id):
    """Get all questions with user's progress"""
    company = request.args.get('company')
    difficulty = request.args.get('difficulty')
    topic = request.args.get('topic')
    status = request.args.get('status')
    search = request.args.get('search')

    with get_db() as conn:
        cursor = conn.cursor()

        query = '''
            SELECT q.*, p.status, p.best_time, p.attempt_count, 
                   p.date_completed, p.notes
            FROM questions q
            LEFT JOIN progress p ON q.id = p.question_id AND p.user_id = %s
            WHERE 1=1
        '''
        params = [user_id]

        if difficulty:
            query += ' AND q.difficulty = %s'
            params.append(difficulty.upper())

        if status:
            if status == 'unattempted':
                query += ' AND (p.status IS NULL OR p.status = %s)'
            else:
                query += ' AND p.status = %s'
            params.append(status)

        if search:
            query += ' AND (q.name LIKE %s OR JSON_SEARCH(q.topics, "one", %s) IS NOT NULL)'
            search_term = f'%{search}%'
            params.extend([search_term, f'%{search}%'])

        cursor.execute(query, params)
        rows = cursor.fetchall()

        questions = []
        for row in rows:
            question = dict(row)

            # FIX: Parse JSON fields before filtering
            if company:
                companies = question['companies'] if question['companies'] else []
                if isinstance(companies, str):
                    companies = json.loads(companies)
                has_company = any(c['name'].lower() == company.lower() for c in companies)
                if not has_company:
                    continue

            if topic:
                topics = question['topics'] if question['topics'] else []
                if isinstance(topics, str):
                    topics = json.loads(topics)
                has_topic = any(topic.lower() in t.lower() for t in topics)
                if not has_topic:
                    continue

            progress = {
                'status': question['status'],
                'best_time': question['best_time'],
                'attempt_count': question['attempt_count'],
                'date_completed': question['date_completed'],
                'notes': question['notes']
            }

            serialized = serialize_question(
                question,
                progress,
                show_all_companies=False,
                filter_company=company
            )
            questions.append(serialized)

        return jsonify({
            'success': True,
            'count': len(questions),
            'data': questions
        })


@app.route('/api/questions/<int:question_id>', methods=['GET'])
@token_required
def get_question(user_id, question_id):
    """Get specific question with user's progress"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT q.*, p.status, p.best_time, p.attempt_count,
                   p.date_completed, p.notes
            FROM questions q
            LEFT JOIN progress p ON q.id = p.question_id AND p.user_id = %s
            WHERE q.id = %s
        ''', (user_id, question_id))

        row = cursor.fetchone()

        if not row:
            return jsonify({'success': False, 'error': 'Question not found'}), 404

        question = dict(row)
        progress = {
            'status': question['status'],
            'best_time': question['best_time'],
            'attempt_count': question['attempt_count'],
            'date_completed': question['date_completed'],
            'notes': question['notes']
        }

        serialized = serialize_question(question, progress, show_all_companies=True)

        return jsonify({
            'success': True,
            'data': serialized
        })


# ============= PROGRESS TRACKING =============

@app.route('/api/questions/<int:question_id>/status', methods=['PUT'])
@token_required
def update_status(user_id, question_id):
    """Update question status"""
    data = request.json
    status = data.get('status')

    if status not in ['unattempted', 'solved', 'practice']:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM questions WHERE id = %s', (question_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'error': 'Question not found'}), 404

        date_completed = datetime.now(timezone.utc) if status == 'solved' else None

        cursor.execute('''
            INSERT INTO progress (user_id, question_id, status, date_completed, last_attempted)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                last_attempted = VALUES(last_attempted),
                date_completed = CASE 
                    WHEN VALUES(status) = 'solved' AND date_completed IS NULL 
                    THEN VALUES(date_completed) 
                    ELSE date_completed 
                END
        ''', (user_id, question_id, status, date_completed, datetime.now(timezone.utc)))

        conn.commit()

        return jsonify({'success': True, 'message': 'Status updated'})


@app.route('/api/questions/<int:question_id>/time', methods=['PUT'])
@token_required
def update_time(user_id, question_id):
    """Update best time (in seconds)"""
    data = request.json
    best_time = data.get('bestTime')

    if not isinstance(best_time, int) or best_time < 0:
        return jsonify({'success': False, 'error': 'Invalid time. Must be positive integer (seconds)'}), 400

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO progress (user_id, question_id, best_time, last_attempted)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                best_time = VALUES(best_time),
                last_attempted = VALUES(last_attempted)
        ''', (user_id, question_id, best_time, datetime.now(timezone.utc)))

        conn.commit()

        return jsonify({
            'success': True,
            'message': 'Time updated',
            'bestTime': best_time,
            'bestTimeFormatted': format_time(best_time)
        })


@app.route('/api/questions/<int:question_id>/notes', methods=['PUT'])
@token_required
def update_notes(user_id, question_id):
    """Update notes"""
    data = request.json
    notes = data.get('notes')

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO progress (user_id, question_id, notes, last_attempted)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                notes = VALUES(notes),
                last_attempted = VALUES(last_attempted)
        ''', (user_id, question_id, notes, datetime.now(timezone.utc)))

        conn.commit()

        return jsonify({'success': True, 'message': 'Notes updated'})


@app.route('/api/questions/<int:question_id>/attempt', methods=['POST'])
@token_required
def increment_attempt(user_id, question_id):
    """Increment attempt count"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO progress (user_id, question_id, attempt_count, last_attempted)
            VALUES (%s, %s, 1, %s)
            ON DUPLICATE KEY UPDATE
                attempt_count = attempt_count + 1,
                last_attempted = VALUES(last_attempted)
        ''', (user_id, question_id, datetime.now(timezone.utc)))

        conn.commit()

        cursor.execute('SELECT attempt_count FROM progress WHERE user_id = %s AND question_id = %s',
                       (user_id, question_id))
        result = cursor.fetchone()
        count = result['attempt_count'] if result else 0

        return jsonify({'success': True, 'attemptCount': count})


# ============= STATISTICS =============

@app.route('/api/stats', methods=['GET'])
@token_required
def get_stats(user_id):
    """Get user's overall statistics"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) as total FROM questions')
        total = cursor.fetchone()['total']

        cursor.execute('''
            SELECT 
                COUNT(CASE WHEN p.status = 'solved' THEN 1 END) as solved,
                COUNT(CASE WHEN p.status = 'practice' THEN 1 END) as practice,
                COUNT(CASE WHEN p.status IS NULL OR p.status = 'unattempted' THEN 1 END) as unattempted
            FROM questions q
            LEFT JOIN progress p ON q.id = p.question_id AND p.user_id = %s
        ''', (user_id,))
        status_stats = dict(cursor.fetchone())

        cursor.execute('''
            SELECT 
                q.difficulty,
                COUNT(*) as total,
                COUNT(CASE WHEN p.status = 'solved' THEN 1 END) as solved
            FROM questions q
            LEFT JOIN progress p ON q.id = p.question_id AND p.user_id = %s
            GROUP BY q.difficulty
        ''', (user_id,))
        difficulty_stats = [dict(row) for row in cursor.fetchall()]

        cursor.execute('''
            SELECT AVG(best_time) as avg_time
            FROM progress
            WHERE user_id = %s AND best_time IS NOT NULL
        ''', (user_id,))
        avg_result = cursor.fetchone()
        avg_time = float(avg_result['avg_time']) if avg_result and avg_result['avg_time'] else None

        return jsonify({
            'success': True,
            'data': {
                'total': total,
                'byStatus': status_stats,
                'byDifficulty': difficulty_stats,
                'averageTime': avg_time,
                'averageTimeFormatted': format_time(int(avg_time)) if avg_time else None
            }
        })


@app.route('/api/stats/company/<company_name>', methods=['GET'])
@token_required
def get_company_stats(user_id, company_name):
    """Get user's statistics for specific company"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT id, companies FROM questions')
        rows = cursor.fetchall()

        company_question_ids = []
        for row in rows:
            # FIX: Parse JSON field before filtering
            companies = row['companies'] if row['companies'] else []
            if isinstance(companies, str):
                companies = json.loads(companies)
            if any(c['name'].lower() == company_name.lower() for c in companies):
                company_question_ids.append(row['id'])

        if not company_question_ids:
            return jsonify({
                'success': True,
                'data': {'total': 0, 'solved': 0, 'practice': 0, 'unattempted': 0}
            })

        placeholders = ','.join(['%s'] * len(company_question_ids))
        params = [user_id] + company_question_ids

        cursor.execute(f'''
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN p.status = 'solved' THEN 1 END) as solved,
                COUNT(CASE WHEN p.status = 'practice' THEN 1 END) as practice,
                COUNT(CASE WHEN p.status IS NULL OR p.status = 'unattempted' THEN 1 END) as unattempted
            FROM questions q
            LEFT JOIN progress p ON q.id = p.question_id AND p.user_id = %s
            WHERE q.id IN ({placeholders})
        ''', params)

        stats = dict(cursor.fetchone())

        return jsonify({
            'success': True,
            'data': stats
        })


# ============= DATA IMPORT (ADMIN) =============

@app.route('/api/import', methods=['POST'])
@admin_required
def import_questions(user_id):
    """Import questions from JSON"""
    data = request.json
    questions = data.get('questions', [])

    with get_db() as conn:
        cursor = conn.cursor()
        imported = 0
        skipped = 0

        for q in questions:
            try:
                cursor.execute('''
                    INSERT IGNORE INTO questions 
                    (name, acceptance_rate, average_frequency, topics, link, difficulty, companies)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (
                    q['name'],
                    q.get('acceptanceRate'),
                    q.get('averageFrequency'),
                    json.dumps(q.get('topics', [])),
                    q['link'],
                    q['difficulty'],
                    json.dumps(q.get('companies', []))
                ))
                if cursor.rowcount > 0:
                    imported += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"Error importing question: {e}")
                skipped += 1

        conn.commit()

        return jsonify({
            'success': True,
            'message': f'Imported {imported} questions, skipped {skipped} duplicates',
            'imported': imported,
            'skipped': skipped
        })

@app.route('/api/questions/<int:question_id>/topics', methods=['DELETE'])
@admin_required
def remove_topics(user_id, question_id):
    """Remove specific topics from a question (Admin only)"""
    data = request.json
    topics_to_remove = data.get('topics', [])

    if not topics_to_remove:
        return jsonify({'success': False, 'error': 'No topics specified'}), 400

    with get_db() as conn:
        cursor = conn.cursor()

        # Get current question
        cursor.execute('SELECT id, topics FROM questions WHERE id = %s', (question_id,))
        question = cursor.fetchone()

        if not question:
            return jsonify({'success': False, 'error': 'Question not found'}), 404

        # FIX: Parse JSON field
        current_topics = question['topics'] if question['topics'] else []
        if isinstance(current_topics, str):
            current_topics = json.loads(current_topics)

        # Remove specified topics (case-insensitive)
        topics_to_remove_lower = [t.lower() for t in topics_to_remove]
        updated_topics = [t for t in current_topics if t.lower() not in topics_to_remove_lower]

        # Update question
        cursor.execute('''
            UPDATE questions 
            SET topics = %s 
            WHERE id = %s
        ''', (json.dumps(updated_topics), question_id))

        conn.commit()

        removed_count = len(current_topics) - len(updated_topics)

        return jsonify({
            'success': True,
            'message': f'Removed {removed_count} topic(s)',
            'removedCount': removed_count,
            'updatedTopics': updated_topics
        })


# ============= HEALTH CHECK =============

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as count FROM questions')
            question_count = cursor.fetchone()['count']

            cursor.execute('SELECT COUNT(*) as count FROM users')
            user_count = cursor.fetchone()['count']

        return jsonify({
            'success': True,
            'message': 'API is running',
            'database': 'connected',
            'questions': question_count,
            'users': user_count
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': 'Database connection failed',
            'error': str(e)
        }), 500


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with API info"""
    return jsonify({
        'name': 'LeetCode Tracker API (Multi-User)',
        'version': '2.0.0',
        'status': 'running',
        'database': 'MySQL',
        'endpoints': {
            'auth': {
                'register': 'POST /api/auth/register',
                'login': 'POST /api/auth/login',
                'me': 'GET /api/auth/me'
            },
            'questions': 'GET /api/questions (requires auth)',
            'stats': 'GET /api/stats (requires auth)',
            'health': '/api/health'
        }
    })


if __name__ == '__main__':
    # Initialize database tables on startup
    try:
        init_db()
        populate_initial_data()
    except Exception as e:
        print(f"Error initializing database: {e}")
    
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)