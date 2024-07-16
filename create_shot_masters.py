'''
Create Shot Masters
part of shotgrid-flame-tools
by Andrew Miller

version: 0.1.0
only tested on flame 2023.3 and higher

Creates delivery master sequences for each shot on a sequence in flame.

contact: andrew.miller@mtifilm.com
'''

import flame, os, shutil
from datetime import date

# Path and environment var constants to minimize os forking at run time
SCRIPT_PATH = os.path.dirname(__file__)
TEMP_FOLDER = os.path.join(SCRIPT_PATH, 'temp')

SLATE_BG_PATH = os.environ.get('SLATE_BG_PATH', os.path.join(SCRIPT_PATH, 'slate_bg_samples', 'mti_slate_bg_RESOLUTION.dpx'))
SLATE_TEMPLATE_PATH = os.environ.get('SLATE_TEMPLATE_PATH', os.path.join(SCRIPT_PATH, 'setups', 'slate_examples', 'slate_template_RESOLUTION.ttg'))

MEZZANINE_PRESET = os.path.join(SCRIPT_PATH, 'export_presets', 'Deliverable Mezzanines.xml')
THUMBNAIL_SETUP = os.path.join(SCRIPT_PATH, 'setups', 'timeline_thumbnail.action')

def build_shot_masters_from_sequence(selection):
    '''
    Takes a sequence of completed shots and creates online and offline slated master sequences using information from Shotgrid and the clips' metadata. 
    DPX sequences are then rendered and re-imported for review and delivery.

    :selection: flame.PySequence object passed from Flame's get_media_panel_custom_ui_actions() hook
    '''
    
    import sgtk

    flame_engine = sgtk.platform.current_engine()

    # Slate generation depends on shotgrid access so bail out if the flame project is not linked to SG
    if flame_engine is None :
        return
    
    sg_auth = sgtk.get_authenticated_user()
    shotgrid = sg_auth.create_sg_connection()

    # Init some flame object vars relative to the selected sequence
    sequence = selection[0]
    reel_group = sequence.parent.parent
    temp_reel = reel_group.create_reel('slate elements')
    submission_name = sequence.name.get_value()
    offline_reel = reel_group.create_reel(submission_name)
    online_reel = reel_group.create_reel(submission_name.replace('comp_submissions', 'mti'))

    resolutions = []
    shot_names = {}

    offline_bg = flame.import_clips(SLATE_BG_PATH.replace('RESOLUTION', '1920x1080'), temp_reel)[0]

    for shot in collect_sequence_segments(sequence):
        shot_names[shot.name.get_value()] = shot.shot_name.get_value()

        # Create PySequence objects for both master versions
        online_master = shot.match(online_reel).open_as_sequence()
        offline_master = create_offline_sequence(shot, offline_reel, temp_reel)
        
        # Check if the correct resolution slate background has been imported and import it if not
        resolution = f'{online_master.width}x{online_master.height}'
        if resolution not in resolutions:
            online_bg = flame.import_clips(SLATE_BG_PATH.replace('RESOLUTION', resolution), temp_reel)[0]
            resolutions.append(resolution)
        else :
            online_bg = [clip for clip in temp_reel.clips if clip.name.get_value().endswith(resolution)][0]
            
        # Grab a frame from the clip to use as the slate thumbnails
        online_thumbnail_clip = extract_thumbnail(shot, temp_reel)
        offline_thumbnail_clip = extract_thumbnail(shot, temp_reel, effects = True)

        # Cut the slate background and the thumnbail onto frame 0 of each master
        insert_slate_frame(online_master, shot, online_bg, online_thumbnail_clip)
        insert_slate_frame(offline_master, shot, offline_bg, offline_thumbnail_clip)

        # Fill the slates up with the proper info 
        slate_info = get_slate_info(shot, shotgrid)
        slate_info['<PROJECT>'] = flame_engine.context.project['name']

        slate_info['<RESOLUTION>'] = resolution
        slate_info['<COLOR_SPACE>'] = online_master.get_colour_space()
        generate_slate(online_master, slate_info)

        slate_info['<RESOLUTION>'] = '1920x1080'
        slate_info['<COLOR_SPACE>'] = 'Rec.709'
        generate_slate(offline_master, slate_info)

    # clean up temp folders in flame and on disk
    flame.delete(temp_reel)
    delete_temp_folder()

    archive_library = get_sequences_folders(reel_group)     # library in flame to move the master sequences too once they've been rendered out

    # Render out DPX for all the sequences. 
    # DPX/file sequence seems to be important for getting flame to write the sequence name into the tape field to maintain proper metadata.
    # If you export the sequences directly to ProRes or DNx, they'll all end up with 'untitled' as the tape name.
    flame.browser.show('/data', select_directory = True, title = 'Select Destination')
    mezzanines_path = flame.browser.selection[0]

    if not mezzanines_path :
        return flame.messages.show_in_console('Mezzanine export cancelled', type = 'error', duration = 5)

    online_mezzanines = export_mezzanines(online_reel, mezzanines_path)
    offline_mezzanines = export_mezzanines(offline_reel, mezzanines_path)
    
    # Reimport the mezzanine files
    def import_online_mezzanines():
        flame.media_panel.move(online_reel.sequences, archive_library['online'])
        flame.import_clips(online_mezzanines, online_reel)

    def import_offline_mezzanines():
        flame.media_panel.move(offline_reel.sequences, archive_library['offline'])
        flame.import_clips(offline_mezzanines, offline_reel)
        for clip in offline_reel.clips:
            clip.versions[0].tracks[0].segments[0].shot_name = shot_names[clip.name.get_value()]

    flame.schedule_idle_event(import_online_mezzanines, delay = 1)
    flame.schedule_idle_event(import_offline_mezzanines, delay = 1)

    return

def get_sequences_folders(reel_group):
    '''
    Fetches dict of online and offline folders in a 'Shot Sequences' library to archive master sequences to once they've been rendered. 
    Creates the library and folders if they don't already exist.

    :reel_group: PyReelGroup object 
    '''

    workspace = reel_group.parent.parent

    for library in workspace.libraries :
        if 'Shot Sequences' in library.name.get_value() :
            return {folder.name.get_value(): folder for folder in library.folders}
        
    sequences_library = reel_group.parent.parent.create_library('Shot Sequences')

    return {'online': sequences_library.create_folder('online'),
            'offline': sequences_library.create_folder('offline')}


def export_mezzanines(reel, path):
    '''
    Creates a custom flame exporter to render out DPX files of all the sequences in the provided reel

    :reel: PyReel containing shot master sequences
    :path: path to render the DPX into

    returns the path of the containing folder for the rendered DPX
    '''

    exporter = flame.PyExporter()
    exporter.foreground = True

    exporter.export(reel, MEZZANINE_PRESET, path)
    
    return os.path.join(path, reel.name.get_value())


def insert_slate_frame(sequence, shot, bg, thumbnail_clip):
    '''
    Inserts a slate with a thumbnail as the first frame before the head handles of a shot.

    A slate background with a text effect gets placed on track 1 while the thumbnail frame of the shot itself is placed on track 2 and sized down with an Action effect.

    :sequence: the PySequence object to add the slate to
    :shot: the original PySegment object the sequence was created from. Necessary for the overwrite operation to work properly. 
    :bg: PyClip object of the slate background image
    :thumbnail_clip: PyClip of the frame to use as the thumbnail

    returns the slated sequence
    '''

    sequence.overwrite(bg, flame.PyTime(0), sequence.versions[0].tracks[0])
    sequence.overwrite(thumbnail_clip, flame.PyTime(1 - shot.head), sequence.versions[0].create_track())

    thumb_segment = sequence.versions[0].tracks[1].segments[0]
    thumbnail = thumb_segment.create_effect('Action')
    thumb_segment.effects[0].bypass = False
    thumbnail.load_setup(THUMBNAIL_SETUP)

    return sequence

def collect_sequence_segments(sequence):
    '''
    Creates a list of all the video segments on a sequence.

    :sequence: PySequence object to parse

    returns list of PySegments
    '''

    segments = []

    for version in sequence.versions :
        for track in version.tracks :
            for segment in track.segments :
                segments.append(segment)

    return [segment for segment in segments if segment.type == 'Video Segment'] 

def create_offline_sequence(shot, sequence_reel, temp_reel):
    '''
    Creates a 1920x1080 PySequence from a higher resolution PySegment

    :shot: PySegment from a sequence of shots to be delivered
    :sequence_reel: the PyReel that will contain all offline master sequences for the batch of shots
    :temp_reel: a temporary PyReel to stage the source clip in before sequence creation

    returns 1920x1080 PySequence
    '''

    match_clip = shot.match(temp_reel, include_timeline_fx = True)

    offline_sequence = sequence_reel.create_sequence(name = match_clip.name.get_value(), 
                                                     width = 1920, 
                                                     height = 1080, 
                                                     bit_depth = 16, 
                                                     scan_mode = 'P', 
                                                     frame_rate = '23.976 fps', 
                                                     start_at = match_clip.start_time.get_value(), 
                                                     audio_tracks = 0)
    
    offline_sequence.overwrite(match_clip)

    return offline_sequence
    
def extract_thumbnail(shot, destination_reel, effects = False):
    '''
    Creates a PyClip with a 1-frame duration to use as a slate thumbnail by matching it into a reel and setting in and out marks around the first frame used in the edit.

    :shot: PySegment to grab thumbnail from
    :destination: PyReel to place thumbnail clip into
    :effects: bool to determine whether to preserve any timeline FX that appear on the original segment. default: False

    returns PyClip with in and out surrounding the thumbnail frame
    '''

    thumb_clip = shot.match(destination_reel, include_timeline_fx = effects)
    thumb_clip.in_mark = shot.head + 1
    thumb_clip.out_mark = shot.head + 2

    return thumb_clip

def load_slate_setup(resolution):
    '''
    Load all the lines of a Text FX template setup into a python list

    :resolution: string in the format '{width}x{height}' used to load in the correct setup file for the media being slated

    returns list of .ttg lines as strings
    '''

    with open(SLATE_TEMPLATE_PATH.replace('RESOLUTION', resolution), 'r') as template:
        setup_lines = template.readlines()
        
    return setup_lines

def generate_slate(shot_sequence, slate_info):
    '''
    Logic for filling in a slate template setup with relevant info for the provided shot master.

    :shot_sequence: PySequence of a shot master where we will load the slate setup
    :slate_info: dict that maps generic template tags to their shot specific values. Use get_slate_info() to generate.

    returns Text PyTimelineFX applied to a slate background PySegment
    '''

    resolution = slate_info['<RESOLUTION>']
    slate_setup = load_slate_setup(resolution)
    text_lines = {index: line for index, line in enumerate(slate_setup) if line.startswith('Text')}

    for token, value in slate_info.items() :
        new_lines = replace_token(token, value, text_lines)
        
        slate_setup = update_setup(slate_setup, new_lines)
    
    new_setup_path = write_slate_ttg(slate_setup, f'{slate_info["<CODE>"]}-{resolution}')

    slate = shot_sequence.versions[0].tracks[0].segments[0].create_effect('Text')
    slate.load_setup(new_setup_path)

    return slate

def get_slate_info(shot, shotgrid):
    '''
    Creates a dictionary that maps the tags from a slate template to the specific values of a supplied shot using the flame and shotgrid APIs

    :shot: PySegment to generate slate for
    :shotgrid: shotgrid API

    returns dict of format {'<TAG>': 'value'}
    '''
    version_name = shot.name.get_value()
    shot_entry = shotgrid.find_one('Shot', [['code', 'is', shot.shot_name.get_value()]], ['description'])
    version_entry = shotgrid.find_one('Version', [['code', 'is', version_name]], ['user','description'])

    version_components = version_name.split('_')
    slate_info = {}

    slate_info['<CODE>'] = shot.shot_name.get_value()
    slate_info['<DESCRIPTION>'] = shot_entry['description']
    slate_info['<TYPE>'] = version_components[-2]
    slate_info['<VERSION>'] = version_components[-1]
    slate_info['<CURRENT_DATE>'] = date.today().strftime('%-m/%-d/%Y')
    slate_info['<ARTIST>'] = version_entry['user']['name']
    slate_info['<DURATION>'] = f'{shot.source_duration.frame} frames'
    slate_info['<HANDLES>'] = f'{shot.head} frames'
    slate_info['<FILE_NAME>'] = f'{version_name}.mov'
    slate_info['<NOTES>'] = version_entry['description']

    return slate_info

def ascii_convert(text_to_convert):
    '''
    Converts a string of text to a string of the unicode code values representing it.
    Adapted from Mike Vaglienty's slate maker script.

    :text_to_convert: string of text

    returns string of space-seperated int unicode values
    '''

    text_to_convert = text_to_convert.replace('“', '"').replace('”', '"')

    ascii_list = []

    for char in text_to_convert:
        ascii_num = ord(char)
        if ascii_num != 194:
            ascii_list.append(ascii_num)

    ascii_code = ' '.join(str(a) for a in ascii_list)

    return ascii_code

def replace_token(token, value, text_lines):
    '''
    Searches for a template token in a text FX setup and replaces it with a supplied value

    :token: string of a template tag eg. '<RESOLUTION>'
    :value: string to insert into setup eg '3840x2160'

    returns dict mapping the updated lines of the setup to thier line number
    '''

    if not value :
        value = ' '

    char_count = len(value)
    pattern = ascii_convert(token)
    pattern_replacement = ascii_convert(value)

    new_lines = {}
    
    for index, line in text_lines.items():
        if pattern in line:
            new_lines[index] = line.replace(pattern, pattern_replacement)
            new_lines[index - 1] = f'TextLength {char_count}\n'

    return new_lines
        
def write_slate_ttg(setup_lines, file_name):
    '''
    Creates a text setup .ttg file from strings in a list

    :setup_lines: list of strings representing the text setup
    :file_name: string of the desired name for the text setup

    returns string of the path of the generated .ttg file
    '''

    if not os.path.exists(TEMP_FOLDER):
        os.makedirs(TEMP_FOLDER)

    ttg_path = os.path.join(TEMP_FOLDER, f'{file_name}.ttg')

    with open(ttg_path, 'w') as new_setup :
        new_setup.writelines(setup_lines)

    return ttg_path

def update_setup(setup, new_lines) :
    '''
    Replaces the lines of a text setup stored in a list with new values from a dictionary of template-tag replacements.

    :setup: template setup as a list of strings
    :new_lines: dict mapping updated setup lines to their index/line number

    returns setup list with the updates applied
    '''

    for index, value in new_lines.items() :
        setup[index] = value

    return setup

def delete_temp_folder():
    '''Deletes the temporary folder on disk used to store setup files for slates.'''

    if os.path.exists(TEMP_FOLDER):
        shutil.rmtree(TEMP_FOLDER)

def scope_sequence(selection):
    if len(selection) != 1:
        return False
    if not isinstance(selection[0], flame.PySequence) :
        return False
    if not selection[0].name.get_value().endswith('comp_submissions'):
        return False
    else:
        return True

def get_media_panel_custom_ui_actions():

    return [
        {
            "name": "MTI",
            "actions": [
                {
                    "name": "Create Shot Masters",
                    "isVisible": scope_sequence,
                    "execute": build_shot_masters_from_sequence,
                    "minimumVersion": "2023"
                },
            ]
        }
    ]