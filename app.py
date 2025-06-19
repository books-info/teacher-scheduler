from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime, time
import re

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Database configuration
DATABASE = 'scheduling.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            UNIQUE(name, phone)
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            active BOOLEAN DEFAULT 1
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS timeframes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timeframe TEXT NOT NULL UNIQUE,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            CHECK(start_time < end_time)
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_number TEXT NOT NULL UNIQUE
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            timeframe_id INTEGER NOT NULL,
            room_id INTEGER NOT NULL,
            batch_number TEXT NOT NULL,
            days TEXT NOT NULL,
            teacher_ids TEXT NOT NULL,
            active BOOLEAN DEFAULT 1,
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (timeframe_id) REFERENCES timeframes(id),
            FOREIGN KEY (room_id) REFERENCES rooms(id),
            UNIQUE(course_id, batch_number)
        )""")
        
        db.commit()

init_db()

# Helper functions
def parse_12h_time(time_str):
    """Parse 12-hour time string with optional AM/PM into 24-hour time object"""
    if not time_str:
        return None
    
    # Try parsing as 24-hour format first
    try:
        return datetime.strptime(time_str, '%H:%M').time()
    except ValueError:
        pass
    
    # Try parsing as 12-hour format with AM/PM
    try:
        time_str = time_str.upper().replace(' ', '')
        if 'AM' in time_str or 'PM' in time_str:
            time_part = time_str.replace('AM', '').replace('PM', '')
            period = 'AM' if 'AM' in time_str else 'PM'
            time_obj = datetime.strptime(time_part, '%I:%M').time()
            
            # Convert to 24-hour format
            if period == 'PM' and time_obj.hour < 12:
                return time(time_obj.hour + 12, time_obj.minute)
            elif period == 'AM' and time_obj.hour == 12:
                return time(0, time_obj.minute)
            return time_obj
    except ValueError:
        pass
    
    return None

def format_time_12h(time_str):
    """Format 24-hour time string to 12-hour format"""
    try:
        time_obj = datetime.strptime(time_str, '%H:%M').time()
        period = 'AM' if time_obj.hour < 12 else 'PM'
        hour = time_obj.hour % 12
        if hour == 0:
            hour = 12
        return f"{hour}:{time_obj.minute:02d} {period}"
    except:
        return time_str

def validate_time(time_str):
    """Validate and normalize time string (accepts both 12h and 24h formats)"""
    time_obj = parse_12h_time(time_str)
    return time_obj.strftime('%H:%M') if time_obj else None

def check_schedule_conflict(teacher_ids, days, timeframe_id, exclude_batch_id=None):
    db = get_db()
    try:
        # Get the timeframe being checked
        timeframe = db.execute(
            "SELECT start_time, end_time FROM timeframes WHERE id = ?",
            (timeframe_id,)
        ).fetchone()
        
        if not timeframe:
            return {"error": "Timeframe not found"}
            
        new_start = parse_12h_time(timeframe['start_time'])
        new_end = parse_12h_time(timeframe['end_time'])
        
        if not new_start or not new_end:
            return {"error": "Invalid timeframe format"}
            
        # Check each teacher for each day
        for teacher_id in teacher_ids:
            for day in days:
                # Find conflicting batches
                query = """
                    SELECT b.id, t.timeframe, t.start_time, t.end_time 
                    FROM batches b
                    JOIN timeframes t ON b.timeframe_id = t.id
                    WHERE b.days LIKE ? 
                    AND b.teacher_ids LIKE ?
                    AND b.active = 1
                """
                params = [f'%{day}%', f'%{teacher_id}%']
                
                if exclude_batch_id:
                    query += " AND b.id != ?"
                    params.append(exclude_batch_id)
                    
                batches = db.execute(query, params).fetchall()
                
                for batch in batches:
                    existing_start = parse_12h_time(batch['start_time'])
                    existing_end = parse_12h_time(batch['end_time'])
                    
                    if (existing_start and existing_end and 
                        new_start < existing_end and 
                        new_end > existing_start):
                        return {
                            "conflict": True,
                            "teacher_id": teacher_id,
                            "day": day,
                            "conflicting_batch": batch['id'],
                            "timeframe": batch['timeframe']
                        }
        
        return {"conflict": False}
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()

# API Endpoints
@app.route('/api/teachers', methods=['GET', 'POST'])
def teachers():
    db = get_db()
    try:
        if request.method == 'POST':
            data = request.get_json()
            
            if not data or not data.get('name') or not data.get('phone'):
                return jsonify({"error": "Name and phone are required"}), 400
            
            try:
                cursor = db.execute(
                    "INSERT INTO teachers (name, phone) VALUES (?, ?)",
                    (data['name'].strip(), data['phone'].strip())
                )
                db.commit()
                return jsonify({
                    "id": cursor.lastrowid,
                    "name": data['name'],
                    "phone": data['phone']
                }), 201
            except sqlite3.IntegrityError:
                return jsonify({"error": "Teacher with this name and phone already exists"}), 400
        
        teachers = db.execute("SELECT id, name, phone FROM teachers ORDER BY name").fetchall()
        return jsonify([dict(t) for t in teachers])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/teachers/<int:id>', methods=['DELETE'])
def delete_teacher(id):
    db = get_db()
    try:
        db.execute("DELETE FROM teachers WHERE id = ?", (id,))
        db.commit()
        return jsonify({"message": "Teacher deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/courses', methods=['GET', 'POST'])
def courses():
    db = get_db()
    try:
        if request.method == 'POST':
            data = request.get_json()
            
            if not data or not data.get('name'):
                return jsonify({"error": "Course name is required"}), 400
            
            try:
                cursor = db.execute(
                    "INSERT INTO courses (name, description) VALUES (?, ?)",
                    (data['name'].strip(), data.get('description', '').strip())
                )
                db.commit()
                return jsonify({
                    "id": cursor.lastrowid,
                    "name": data['name'],
                    "description": data.get('description', ''),
                    "active": True
                }), 201
            except sqlite3.IntegrityError:
                return jsonify({"error": "Course with this name already exists"}), 400
        
        courses = db.execute("SELECT id, name, description, active FROM courses ORDER BY name").fetchall()
        return jsonify([dict(c) for c in courses])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/courses/<int:id>', methods=['DELETE'])
def delete_course(id):
    db = get_db()
    try:
        db.execute("DELETE FROM courses WHERE id = ?", (id,))
        db.commit()
        return jsonify({"message": "Course deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/timeframes', methods=['GET', 'POST'])
def timeframes():
    db = get_db()
    try:
        if request.method == 'POST':
            data = request.get_json()
            
            if not data or not data.get('start_time') or not data.get('end_time'):
                return jsonify({"error": "Start and end times are required"}), 400
            
            start_time = validate_time(data['start_time'])
            end_time = validate_time(data['end_time'])
            
            if not start_time or not end_time:
                return jsonify({"error": "Invalid time format (use HH:MM or HH:MM AM/PM)"}), 400
            
            if start_time >= end_time:
                return jsonify({"error": "Start time must be before end time"}), 400
            
            timeframe_str = f"{format_time_12h(start_time)} - {format_time_12h(end_time)}"
            
            try:
                cursor = db.execute(
                    "INSERT INTO timeframes (timeframe, start_time, end_time) VALUES (?, ?, ?)",
                    (timeframe_str, start_time, end_time)
                )
                db.commit()
                return jsonify({
                    "id": cursor.lastrowid,
                    "timeframe": timeframe_str,
                    "start_time": start_time,
                    "end_time": end_time
                }), 201
            except sqlite3.IntegrityError:
                return jsonify({"error": "This timeframe already exists"}), 400
        
        timeframes = db.execute("SELECT id, timeframe, start_time, end_time FROM timeframes ORDER BY start_time").fetchall()
        
        # Format times for display
        formatted_timeframes = []
        for tf in timeframes:
            tf_dict = dict(tf)
            tf_dict['display_timeframe'] = f"{format_time_12h(tf['start_time'])} - {format_time_12h(tf['end_time'])}"
            formatted_timeframes.append(tf_dict)
            
        return jsonify(formatted_timeframes)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/timeframes/<int:id>', methods=['DELETE'])
def delete_timeframe(id):
    db = get_db()
    try:
        # Check if timeframe is used in any batches
        batches = db.execute(
            "SELECT COUNT(*) as count FROM batches WHERE timeframe_id = ?",
            (id,)
        ).fetchone()
        
        if batches['count'] > 0:
            return jsonify({"error": "Cannot delete timeframe used in existing batches"}), 400
            
        db.execute("DELETE FROM timeframes WHERE id = ?", (id,))
        db.commit()
        return jsonify({"message": "Timeframe deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/rooms', methods=['GET', 'POST'])
def rooms():
    db = get_db()
    try:
        if request.method == 'POST':
            data = request.get_json()
            
            if not data or not data.get('room_number'):
                return jsonify({"error": "Room number is required"}), 400
            
            try:
                cursor = db.execute(
                    "INSERT INTO rooms (room_number) VALUES (?)",
                    (data['room_number'].strip(),)
                )
                db.commit()
                return jsonify({
                    "id": cursor.lastrowid,
                    "room_number": data['room_number']
                }), 201
            except sqlite3.IntegrityError:
                return jsonify({"error": "Room with this number already exists"}), 400
        
        rooms = db.execute("SELECT id, room_number FROM rooms ORDER BY room_number").fetchall()
        return jsonify([dict(r) for r in rooms])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/rooms/<int:id>', methods=['DELETE'])
def delete_room(id):
    db = get_db()
    try:
        # Check if room is used in any batches
        batches = db.execute(
            "SELECT COUNT(*) as count FROM batches WHERE room_id = ?",
            (id,)
        ).fetchone()
        
        if batches['count'] > 0:
            return jsonify({"error": "Cannot delete room used in existing batches"}), 400
            
        db.execute("DELETE FROM rooms WHERE id = ?", (id,))
        db.commit()
        return jsonify({"message": "Room deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/batches', methods=['GET', 'POST'])
def batches():
    db = get_db()
    try:
        if request.method == 'POST':
            data = request.get_json()
            
            # Validate required fields
            required_fields = ['course_id', 'timeframe_id', 'room_id', 'batch_number', 'days', 'teacher_ids']
            if not all(field in data for field in required_fields):
                return jsonify({"error": "Missing required fields"}), 400
            
            # Convert teacher_ids to list if it's a string
            if isinstance(data['teacher_ids'], str):
                data['teacher_ids'] = [int(id.strip()) for id in data['teacher_ids'].split(',') if id.strip()]
            elif not isinstance(data['teacher_ids'], list):
                return jsonify({"error": "teacher_ids must be a list or comma-separated string"}), 400
            
            # Validate days
            if not isinstance(data['days'], list):
                return jsonify({"error": "days must be a list"}), 400
            valid_days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            if not all(day in valid_days for day in data['days']):
                return jsonify({"error": "Invalid day values"}), 400
            
            # Check for schedule conflicts
            conflict_check = check_schedule_conflict(
                data['teacher_ids'],
                data['days'],
                data['timeframe_id']
            )
            
            if 'error' in conflict_check:
                return jsonify({"error": conflict_check['error']}), 400
            elif conflict_check.get('conflict'):
                return jsonify({
                    "error": "Schedule conflict",
                    "details": conflict_check
                }), 409
            
            # Insert new batch
            try:
                cursor = db.execute(
                    """INSERT INTO batches 
                    (course_id, timeframe_id, room_id, batch_number, days, teacher_ids, active) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        int(data['course_id']),
                        int(data['timeframe_id']),
                        int(data['room_id']),
                        data['batch_number'].strip(),
                        ','.join(data['days']),
                        ','.join(map(str, data['teacher_ids'])),
                        data.get('active', True)
                    )
                )
                db.commit()
                
                # Get the full batch data to return
                batch = db.execute("""
                    SELECT b.id, b.course_id, c.name AS course, b.timeframe_id, 
                           t.timeframe, b.room_id, r.room_number, 
                           b.batch_number, b.days, b.teacher_ids, b.active
                    FROM batches b
                    JOIN courses c ON b.course_id = c.id
                    JOIN timeframes t ON b.timeframe_id = t.id
                    JOIN rooms r ON b.room_id = r.id
                    WHERE b.id = ?
                """, (cursor.lastrowid,)).fetchone()
                
                # Get teacher names
                teachers = {t['id']: t['name'] for t in db.execute("SELECT id, name FROM teachers").fetchall()}
                teacher_ids = [int(id) for id in batch['teacher_ids'].split(',') if id.strip()]
                teacher_names = [teachers.get(id, f"Teacher {id}") for id in teacher_ids]
                
                return jsonify({
                    "id": batch['id'],
                    "course_id": batch['course_id'],
                    "course": batch['course'],
                    "timeframe_id": batch['timeframe_id'],
                    "timeframe": batch['timeframe'],
                    "room_id": batch['room_id'],
                    "room": batch['room_number'],
                    "batch_number": batch['batch_number'],
                    "days": batch['days'].split(','),
                    "teacher_ids": teacher_ids,
                    "teacher_names": teacher_names,
                    "active": bool(batch['active'])
                }), 201
            except sqlite3.IntegrityError as e:
                if "FOREIGN KEY" in str(e):
                    return jsonify({"error": "Invalid course, timeframe, or room ID"}), 400
                elif "UNIQUE" in str(e):
                    return jsonify({"error": "Batch with this number already exists for this course"}), 400
                return jsonify({"error": str(e)}), 400
        
        # GET all batches with expanded information
        batches = db.execute("""
            SELECT b.id, b.course_id, c.name AS course, b.timeframe_id, 
                   t.timeframe, b.room_id, r.room_number, 
                   b.batch_number, b.days, b.teacher_ids, b.active
            FROM batches b
            JOIN courses c ON b.course_id = c.id
            JOIN timeframes t ON b.timeframe_id = t.id
            JOIN rooms r ON b.room_id = r.id
            ORDER BY c.name, b.batch_number
        """).fetchall()
        
        # Get teacher names
        teachers = {t['id']: t['name'] for t in db.execute("SELECT id, name FROM teachers").fetchall()}
        
        formatted_batches = []
        for batch in batches:
            teacher_ids = [int(id) for id in batch['teacher_ids'].split(',') if id.strip()]
            teacher_names = [teachers.get(id, f"Teacher {id}") for id in teacher_ids]
            
            formatted_batches.append({
                "id": batch['id'],
                "course_id": batch['course_id'],
                "course": batch['course'],
                "timeframe_id": batch['timeframe_id'],
                "timeframe": batch['timeframe'],
                "room_id": batch['room_id'],
                "room": batch['room_number'],
                "batch_number": batch['batch_number'],
                "days": batch['days'].split(','),
                "teacher_ids": teacher_ids,
                "teacher_names": teacher_names,
                "active": bool(batch['active'])
            })
        
        return jsonify(formatted_batches)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/batches/<int:id>', methods=['PUT', 'DELETE'])
def manage_batch(id):
    db = get_db()
    try:
        if request.method == 'PUT':
            data = request.get_json()
            
            # Get current batch data
            current_batch = db.execute(
                "SELECT * FROM batches WHERE id = ?", 
                (id,)
            ).fetchone()
            
            if not current_batch:
                return jsonify({"error": "Batch not found"}), 404
                
            # Prepare update data with defaults from current batch if not provided
            update_data = {
                'course_id': data.get('course_id', current_batch['course_id']),
                'timeframe_id': data.get('timeframe_id', current_batch['timeframe_id']),
                'room_id': data.get('room_id', current_batch['room_id']),
                'batch_number': data.get('batch_number', current_batch['batch_number']),
                'days': data.get('days', current_batch['days'].split(',')),
                'teacher_ids': data.get('teacher_ids', [int(id) for id in current_batch['teacher_ids'].split(',') if id.strip()]),
                'active': data.get('active', current_batch['active'])
            }
            
            # Convert teacher_ids if it's a string
            if isinstance(update_data['teacher_ids'], str):
                update_data['teacher_ids'] = [int(id.strip()) for id in update_data['teacher_ids'].split(',') if id.strip()]
            
            # Validate days
            valid_days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            if not all(day in valid_days for day in update_data['days']):
                return jsonify({"error": "Invalid day values"}), 400
            
            # Check for schedule conflicts if timeframe, teachers, or days changed
            if ('timeframe_id' in data or 'teacher_ids' in data or 'days' in data):
                conflict_check = check_schedule_conflict(
                    update_data['teacher_ids'],
                    update_data['days'],
                    update_data['timeframe_id'],
                    id
                )
                
                if 'error' in conflict_check:
                    return jsonify({"error": conflict_check['error']}), 400
                elif conflict_check.get('conflict'):
                    return jsonify({
                        "error": "Schedule conflict",
                        "details": conflict_check
                    }), 409
            
            # Perform update
            db.execute("""
                UPDATE batches SET
                    course_id = ?,
                    timeframe_id = ?,
                    room_id = ?,
                    batch_number = ?,
                    days = ?,
                    teacher_ids = ?,
                    active = ?
                WHERE id = ?
            """, (
                int(update_data['course_id']),
                int(update_data['timeframe_id']),
                int(update_data['room_id']),
                update_data['batch_number'].strip(),
                ','.join(update_data['days']),
                ','.join(map(str, update_data['teacher_ids'])),
                update_data['active'],
                id
            ))
            db.commit()
            
            # Get the updated batch data to return
            batch = db.execute("""
                SELECT b.id, b.course_id, c.name AS course, b.timeframe_id, 
                       t.timeframe, b.room_id, r.room_number, 
                       b.batch_number, b.days, b.teacher_ids, b.active
                FROM batches b
                JOIN courses c ON b.course_id = c.id
                JOIN timeframes t ON b.timeframe_id = t.id
                JOIN rooms r ON b.room_id = r.id
                WHERE b.id = ?
            """, (id,)).fetchone()
            
            # Get teacher names
            teachers = {t['id']: t['name'] for t in db.execute("SELECT id, name FROM teachers").fetchall()}
            teacher_ids = [int(id) for id in batch['teacher_ids'].split(',') if id.strip()]
            teacher_names = [teachers.get(id, f"Teacher {id}") for id in teacher_ids]
            
            return jsonify({
                "id": batch['id'],
                "course_id": batch['course_id'],
                "course": batch['course'],
                "timeframe_id": batch['timeframe_id'],
                "timeframe": batch['timeframe'],
                "room_id": batch['room_id'],
                "room": batch['room_number'],
                "batch_number": batch['batch_number'],
                "days": batch['days'].split(','),
                "teacher_ids": teacher_ids,
                "teacher_names": teacher_names,
                "active": bool(batch['active'])
            }), 200
            
        elif request.method == 'DELETE':
            db.execute("DELETE FROM batches WHERE id = ?", (id,))
            db.commit()
            return jsonify({"message": "Batch deleted successfully"}), 200
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route('/api/dashboard', methods=['GET'])
def dashboard():
    day = request.args.get('day')
    timeframe_id = request.args.get('timeframe_id')
    
    if not day or not timeframe_id:
        return jsonify({"error": "Day and timeframe_id are required parameters"}), 400
    
    db = get_db()
    try:
        # Get timeframe info
        timeframe = db.execute(
            "SELECT timeframe, start_time, end_time FROM timeframes WHERE id = ?",
            (timeframe_id,)
        ).fetchone()
        
        if not timeframe:
            return jsonify({"error": "Timeframe not found"}), 404
        
        # Get all teachers
        teachers = db.execute("SELECT id, name FROM teachers ORDER BY name").fetchall()
        
        # Get batches for this day and timeframe
        batches = db.execute("""
            SELECT b.id, b.teacher_ids, c.name AS course, b.batch_number, r.room_number
            FROM batches b
            JOIN courses c ON b.course_id = c.id
            JOIN rooms r ON b.room_id = r.id
            WHERE b.days LIKE ? AND b.timeframe_id = ? AND b.active = 1
        """, (f'%{day}%', timeframe_id)).fetchall()
        
        # Prepare results
        results = []
        for teacher in teachers:
            teacher_batches = []
            for batch in batches:
                if str(teacher['id']) in batch['teacher_ids'].split(','):
                    teacher_batches.append({
                        "batch_id": batch['id'],
                        "course": batch['course'],
                        "batch_number": batch['batch_number'],
                        "room": batch['room_number']
                    })
            
            results.append({
                "teacher_id": teacher['id'],
                "teacher_name": teacher['name'],
                "status": "busy" if teacher_batches else "free",
                "batches": teacher_batches
            })
        
        return jsonify({
            "day": day,
            "timeframe": timeframe['timeframe'],
            "start_time": format_time_12h(timeframe['start_time']),
            "end_time": format_time_12h(timeframe['end_time']),
            "teachers": results
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

# Frontend serving
@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        init_db()
    app.run(host='0.0.0.0', port=8000, debug=True)