from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import json
import os
from datetime import datetime
from contextlib import contextmanager

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend access

# Use volume path in production, local path in development
DATA_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '.')
DATABASE = os.path.join(DATA_DIR, 'leetcode_tracker.db')

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)


# Database context manager
@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# Initialize database
def init_db():
    with get_db() as conn:
        cursor = conn.cursor()

        # Questions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                acceptance_rate REAL,
                average_frequency REAL,
                topics TEXT,
                link TEXT UNIQUE NOT NULL,
                difficulty TEXT NOT NULL,
                companies TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # User progress table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS progress (
                question_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT 'unattempted',
                best_time TEXT,
                attempt_count INTEGER DEFAULT 0,
                date_completed TIMESTAMP,
                notes TEXT,
                FOREIGN KEY (question_id) REFERENCES questions(id)
            )
        ''')

        conn.commit()


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
                    with open(data_file, 'r') as f:
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
                                cursor.execute('''
                                    INSERT OR IGNORE INTO questions 
                                    (name, acceptance_rate, average_frequency, topics, link, difficulty, companies)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                ''', (
                                    q.get('name'),
                                    q.get('acceptanceRate'),
                                    q.get('averageFrequency'),
                                    json.dumps(q.get('topics', [])),
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
                print("No initial_data.json found. Use /api/import to add questions.")
        else:
            print(f"Database already contains {count} questions. Skipping initial data load.")

# Helper function to serialize question
def serialize_question(question, progress, show_all_companies=False, filter_company=None):
    companies = json.loads(question['companies']) if question['companies'] else []

    # Filter companies if needed
    if not show_all_companies and filter_company:
        companies = [c for c in companies if c['name'].lower() == filter_company.lower()]

    return {
        'id': question['id'],
        'name': question['name'],
        'acceptanceRate': question['acceptance_rate'],
        'averageFrequency': question['average_frequency'],
        'topics': json.loads(question['topics']) if question['topics'] else [],
        'link': question['link'],
        'difficulty': question['difficulty'],
        'companies': companies,
        'status': progress['status'] if progress else 'unattempted',
        'bestTime': progress['best_time'] if progress else None,
        'attemptCount': progress['attempt_count'] if progress else 0,
        'dateCompleted': progress['date_completed'] if progress else None,
        'notes': progress['notes'] if progress else None
    }


# ============= QUESTIONS MANAGEMENT =============

@app.route('/api/questions', methods=['GET'])
def get_questions():
    """Get all questions with filters"""
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
            LEFT JOIN progress p ON q.id = p.question_id
            WHERE 1=1
        '''
        params = []

        # Apply filters
        if difficulty:
            query += ' AND q.difficulty = ?'
            params.append(difficulty.upper())

        if status:
            if status == 'unattempted':
                query += ' AND (p.status IS NULL OR p.status = ?)'
            else:
                query += ' AND p.status = ?'
            params.append(status)

        if search:
            query += ' AND (q.name LIKE ? OR q.topics LIKE ?)'
            search_term = f'%{search}%'
            params.extend([search_term, search_term])

        cursor.execute(query, params)
        rows = cursor.fetchall()

        questions = []
        for row in rows:
            question = dict(row)

            # Filter by company if specified
            if company:
                companies = json.loads(question['companies']) if question['companies'] else []
                has_company = any(c['name'].lower() == company.lower() for c in companies)
                if not has_company:
                    continue

            # Filter by topic if specified
            if topic:
                topics = json.loads(question['topics']) if question['topics'] else []
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

            # Don't show all companies when filtering by company
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
def get_question(question_id):
    """Get specific question with ALL companies"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT q.*, p.status, p.best_time, p.attempt_count,
                   p.date_completed, p.notes
            FROM questions q
            LEFT JOIN progress p ON q.id = p.question_id
            WHERE q.id = ?
        ''', (question_id,))

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

        # Show ALL companies for specific question
        serialized = serialize_question(question, progress, show_all_companies=True)

        return jsonify({
            'success': True,
            'data': serialized
        })


# ============= PROGRESS TRACKING =============

@app.route('/api/questions/<int:question_id>/status', methods=['PUT'])
def update_status(question_id):
    """Update question status"""
    data = request.json
    status = data.get('status')

    if status not in ['unattempted', 'solved', 'practice']:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400

    with get_db() as conn:
        cursor = conn.cursor()

        # Check if question exists
        cursor.execute('SELECT id FROM questions WHERE id = ?', (question_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'error': 'Question not found'}), 404

        # Update or insert progress
        date_completed = datetime.now().isoformat() if status == 'solved' else None

        cursor.execute('''
            INSERT INTO progress (question_id, status, date_completed)
            VALUES (?, ?, ?)
            ON CONFLICT(question_id) DO UPDATE SET
                status = excluded.status,
                date_completed = CASE 
                    WHEN excluded.status = 'solved' AND progress.date_completed IS NULL 
                    THEN excluded.date_completed 
                    ELSE progress.date_completed 
                END
        ''', (question_id, status, date_completed))

        conn.commit()

        return jsonify({'success': True, 'message': 'Status updated'})


@app.route('/api/questions/<int:question_id>/time', methods=['PUT'])
def update_time(question_id):
    """Update best time"""
    data = request.json
    best_time = data.get('bestTime')

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO progress (question_id, best_time)
            VALUES (?, ?)
            ON CONFLICT(question_id) DO UPDATE SET
                best_time = excluded.best_time
        ''', (question_id, best_time))

        conn.commit()

        return jsonify({'success': True, 'message': 'Time updated'})


@app.route('/api/questions/<int:question_id>/notes', methods=['PUT'])
def update_notes(question_id):
    """Add or update notes"""
    data = request.json
    notes = data.get('notes')

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO progress (question_id, notes)
            VALUES (?, ?)
            ON CONFLICT(question_id) DO UPDATE SET
                notes = excluded.notes
        ''', (question_id, notes))

        conn.commit()

        return jsonify({'success': True, 'message': 'Notes updated'})


@app.route('/api/questions/<int:question_id>/attempt', methods=['POST'])
def increment_attempt(question_id):
    """Increment attempt count"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO progress (question_id, attempt_count)
            VALUES (?, 1)
            ON CONFLICT(question_id) DO UPDATE SET
                attempt_count = attempt_count + 1
        ''', (question_id,))

        conn.commit()

        cursor.execute('SELECT attempt_count FROM progress WHERE question_id = ?', (question_id,))
        count = cursor.fetchone()['attempt_count']

        return jsonify({'success': True, 'attemptCount': count})


# ============= STATISTICS =============

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get overall statistics"""
    with get_db() as conn:
        cursor = conn.cursor()

        # Total questions
        cursor.execute('SELECT COUNT(*) as total FROM questions')
        total = cursor.fetchone()['total']

        # By status
        cursor.execute('''
            SELECT 
                COUNT(CASE WHEN p.status = 'solved' THEN 1 END) as solved,
                COUNT(CASE WHEN p.status = 'practice' THEN 1 END) as practice,
                COUNT(CASE WHEN p.status IS NULL OR p.status = 'unattempted' THEN 1 END) as unattempted
            FROM questions q
            LEFT JOIN progress p ON q.id = p.question_id
        ''')
        status_stats = dict(cursor.fetchone())

        # By difficulty
        cursor.execute('''
            SELECT 
                q.difficulty,
                COUNT(*) as total,
                COUNT(CASE WHEN p.status = 'solved' THEN 1 END) as solved
            FROM questions q
            LEFT JOIN progress p ON q.id = p.question_id
            GROUP BY q.difficulty
        ''')
        difficulty_stats = [dict(row) for row in cursor.fetchall()]

        return jsonify({
            'success': True,
            'data': {
                'total': total,
                'byStatus': status_stats,
                'byDifficulty': difficulty_stats
            }
        })


@app.route('/api/stats/company/<company_name>', methods=['GET'])
def get_company_stats(company_name):
    """Get statistics for specific company"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT id, companies FROM questions')
        rows = cursor.fetchall()

        company_question_ids = []
        for row in rows:
            companies = json.loads(row['companies']) if row['companies'] else []
            if any(c['name'].lower() == company_name.lower() for c in companies):
                company_question_ids.append(row['id'])

        if not company_question_ids:
            return jsonify({'success': True, 'data': {'total': 0, 'solved': 0, 'practice': 0, 'unattempted': 0}})

        placeholders = ','.join('?' * len(company_question_ids))
        cursor.execute(f'''
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN p.status = 'solved' THEN 1 END) as solved,
                COUNT(CASE WHEN p.status = 'practice' THEN 1 END) as practice,
                COUNT(CASE WHEN p.status IS NULL OR p.status = 'unattempted' THEN 1 END) as unattempted
            FROM questions q
            LEFT JOIN progress p ON q.id = p.question_id
            WHERE q.id IN ({placeholders})
        ''', company_question_ids)

        stats = dict(cursor.fetchone())

        return jsonify({
            'success': True,
            'data': stats
        })


# ============= DATA IMPORT =============

@app.route('/api/import', methods=['POST'])
def import_questions():
    """Import questions from JSON (from your export script)"""
    data = request.json
    questions = data.get('questions', [])

    with get_db() as conn:
        cursor = conn.cursor()
        imported = 0
        skipped = 0

        for q in questions:
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO questions 
                    (name, acceptance_rate, average_frequency, topics, link, difficulty, companies)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
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


@app.route('/api/import/url', methods=['POST'])
def import_from_url():
    """Import questions from a URL (e.g., GitHub raw file)"""
    import urllib.request

    data = request.json
    url = data.get('url')

    if not url:
        return jsonify({'success': False, 'error': 'URL is required'}), 400

    try:
        with urllib.request.urlopen(url) as response:
            json_data = json.loads(response.read().decode())
            questions = json_data.get('questions', [])

            with get_db() as conn:
                cursor = conn.cursor()
                imported = 0
                skipped = 0

                for q in questions:
                    try:
                        cursor.execute('''
                            INSERT OR IGNORE INTO questions 
                            (name, acceptance_rate, average_frequency, topics, link, difficulty, companies)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
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
                'message': f'Imported {imported} questions from URL, skipped {skipped} duplicates',
                'imported': imported,
                'skipped': skipped
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/reset', methods=['POST'])
def reset_database():
    """Reset database (delete all data) - USE WITH CAUTION"""
    # You might want to add authentication/authorization here
    auth_token = request.headers.get('X-Admin-Token')
    expected_token = os.environ.get('ADMIN_TOKEN', 'your-secret-token-change-this')

    if auth_token != expected_token:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM progress')
        cursor.execute('DELETE FROM questions')
        conn.commit()

    return jsonify({'success': True, 'message': 'Database reset complete'})


# ============= HEALTH CHECK =============

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM questions')
        question_count = cursor.fetchone()['count']

    return jsonify({
        'success': True,
        'message': 'API is running',
        'database': 'connected',
        'questions': question_count
    })


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with API info"""
    return jsonify({
        'name': 'LeetCode Tracker API',
        'version': '1.0.0',
        'status': 'running',
        'endpoints': {
            'health': '/api/health',
            'questions': '/api/questions',
            'stats': '/api/stats',
            'import': '/api/import'
        }
    })


# Initialize database on startup
init_db()
populate_initial_data()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)