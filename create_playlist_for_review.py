'''
Create ShotGrid Playlist for Review
part of shotgrid-flame-tools
by Andrew Miller

version: 0.1.0
only tested on flame 2023.3 and higher

Renders out editorial deliverables from a reel of shot masters and creates a client-reviewable playlist for them on ShotGrid.

contact: andrew.miller@mtifilm.com
'''

import flame, os, time

# Path and environment var constants to minimize os forking at run time
SCRIPT_PATH = os.path.dirname(__file__)
H264_PRESET = os.path.join(SCRIPT_PATH, 'export_presets', 'Shotgrid Version Creation.xml')
DNX36_PRESET = os.path.join(SCRIPT_PATH, 'export_presets', 'Editorial DNx36.xml')

def create_client_delivery(selection):
    '''
    Takes a PyReel in Flame containing slated offline shot masters and creates an h.264 and DNx36 for each.
    The H.264 gets uploaded to ShotGrid where a playlist is created for client review.

    :selection: tuple of media panel entried passed by flame's get_media_panel_custom_ui_actions() hook. limited to a single '_comp_submissions' PyReel.
    '''

    mezzanines_reel = selection[0]      # extract the passed reel from the containing tuple  

    # open a flame browser to set the render destination
    flame.browser.show('/data', select_directory = True, title = 'Select Destination')
    export_path = flame.browser.selection[0]

    # bail out if user doesn't choose a path
    if not export_path :
        return flame.messages.show_in_console('Playlist export cancelled: no path selected', type = 'error', duration = 5)
    
    import sgtk
    flame_engine = sgtk.platform.current_engine()

    # bail out if project isn't linked to ShotGrid
    if flame_engine is None :
        return flame.messages.show_in_console('Playlist export cancelled: project must be linked to ShotGrid', type = 'error', duration = 5)
    
    sg_auth = sgtk.get_authenticated_user()
    project = flame_engine.context.project

    export_editorial_files(mezzanines_reel, export_path)    # render DNx files to the chosen path

    sg_versions = create_versions(mezzanines_reel, project, export_path, sg_auth)       # create version entries in ShotGrid for each shot
    send_h264s_to_shotgrid(mezzanines_reel, sg_versions, export_path, sg_auth)          # upload an H.264 to each shot's ShotGrid version

    create_playlist(mezzanines_reel.name.get_value(), sg_versions, project, sg_auth)    # add the versions to a playlist named after the reel

    return

def create_versions(reel, project, path, sg_authorization):
    '''
    Creates a version in ShotGrid for each shot in a reel using info from its source version.

    :reel: PyReel containing offline shot masters (see create_shot_masters.py for generation)
    :project: Shotgrid project info dictionary extracted from engine context
    :path: path to the versions' H.264s
    :sg_authorization: user authentication for ShotGrid API connection

    returns a list of the created version dictionaries 
    '''

    shotgrid = sg_authorization.create_sg_connection()
    versions = []

    for clip in reel.clips :
        shot_name = clip.versions[0].tracks[0].segments[0].shot_name.get_value()
        shot = shotgrid.find_one('Shot', 
                                 [['code', 'is', shot_name], ['project', 'is', {'type': 'Project', 'id': project['id']}]])

        filters = [['project', 'is', {'type': 'Project', 'id': project['id']}], 
                   ['entity', 'is', {'type': 'Shot', 'id': shot['id']}], 
                   ['content', 'is', 'Comp']]
        task = shotgrid.find_one('Task', filters)

        internal_version = shotgrid.find_one('Version', 
                                             [['code', 'is', clip.name.get_value()]], 
                                             ['description'])

        version_data = {'code': clip.name.get_value(),
                        'entity': {'type': 'Shot', 'id': shot['id']},
                        'sg_task': {'type': 'Task', 'id': task['id']},
                        'project': {'type': 'Project', 'id': project['id']}, 
                        'description': internal_version['description'],
                        'user': sg_authorization.resolve_entity(),
                        'sg_status_list': 'rev',
                        'sg_first_frame': 1001,
                        'sg_last_frame': clip.duration.frame + 999,
                        'frame_count': clip.duration.frame - 1,
                        'sg_movie_aspect_ratio': clip.ratio,
                        'sg_uploaded_movie_frame_rate': float(clip.frame_rate.split()[0]),
                        'sg_movie_has_slate': True,
                        'sg_path_to_movie': os.path.join(path, reel.name.get_value(), 'h264', f'{clip.name.get_value()}.mov')}

        version = shotgrid.create('Version', version_data)

        if version :
            flame.messages.show_in_console(f'Client Delivery: Shotgrid review version created for {clip.name.get_value()}')
            versions.append(version)

    return versions

def send_h264s_to_shotgrid(reel, versions, path, sg_authorization):
    '''
    Renders H.264s of every clip in the reel and uploads them to ShotGrid, linked to a version entry.

    :reel: PyReel containing offline shot masters (see create_shot_masters.py for generation)
    :versions: list of version dicts generated by create_versions() for every shot in the reel
    :path: path to the versions' H.264s
    :sg_authorization: user authentication for ShotGrid API connection
    '''

    exporter = flame.PyExporter()
    exporter.foreground = True

    exporter.export(reel, H264_PRESET, path)

    shotgrid = sg_authorization.create_sg_connection()

    generic_path = os.path.join(path, reel.name.get_value(), 'h264', 'VERSION_NAME.mov')

    for version in versions:
        h264_path = generic_path.replace('VERSION_NAME', version['code'])
        try :
            shotgrid.upload('Version', version['id'], h264_path, field_name = 'sg_uploaded_movie')
        except :
            time.sleep(1)
            shotgrid.upload('Version', version['id'], h264_path, field_name = 'sg_uploaded_movie')

        flame.messages.show_in_console(f'Client Delivery: Movie uploaded to version {version["code"]}')
    return

def export_editorial_files(reel, path):
    '''
    Renders a DNx36 file to the supplied path for every clip in a reel.

    :reel: PyReel containing offline shot masters (see create_shot_masters.py for generation)
    :path: path to export the .mov's to
    '''

    exporter = flame.PyExporter()
    exporter.foreground = True

    exporter.export(reel, DNX36_PRESET, path)

    return flame.messages.show_in_console('Client Delivery: DNx36 Check Files Exported')

def create_playlist(name, versions, project, sg_authorization):
    '''
    Creates a playlist on Shotgrid containing all the supplied version entries.

    :name: string to name the playlist
    :versions: list of version dicts generated by create_versions()
    :project: ShotGrid project info dict
    :sg_authorization: user authentication for ShotGrid API connection
    '''

    shotgrid = sg_authorization.create_sg_connection()    

    playlist_data = {'code': name,
                     'project': {'type': 'Project', 'id': project['id']},
                     'versions': [{'type': 'Version', 'id': version['id']} for version in versions]}
    
    shotgrid.create('Playlist', playlist_data)

    return flame.messages.show_in_console(f'Client Delivery: Playlist "{name}" created')

def scope_reel(selection):
    if len(selection) > 1:
        return False
    if not isinstance(selection[0], flame.PyReel) :
        return False
    if 'comp_submissions' in selection[0].name.get_value():
        return True
    else :
        return False
    
def get_media_panel_custom_ui_actions():
    return [
        {
            'name': 'MTI',
            'actions': [
                {
                    'name': 'Create Client Delivery Playlist',
                    'isVisible': scope_reel,
                    'execute': create_client_delivery,
                },
            ]
        }
    ]