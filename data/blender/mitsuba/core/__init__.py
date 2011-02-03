# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

# System libs
import os, time, threading, subprocess, sys, copy

# Blender libs
import bpy

# Framework libs
from extensions_framework.engine import engine_base
from extensions_framework import util as efutil

# Mitsuba-related classes
from mitsuba import plugin_path
from mitsuba.properties.engine import mitsuba_engine
from mitsuba.properties.sampler import mitsuba_sampler
from mitsuba.properties.integrator import mitsuba_integrator
from mitsuba.properties.lamp import mitsuba_lamp
from mitsuba.properties.texture import mitsuba_texture, \
	mitsuba_tex_ldrtexture, mitsuba_tex_checkerboard, \
	mitsuba_tex_gridtexture, mitsuba_tex_mapping
from mitsuba.properties.material import mitsuba_material, \
	mitsuba_mat_lambertian, mitsuba_mat_phong, mitsuba_mat_ward, \
	mitsuba_mat_microfacet, mitsuba_mat_roughglass, \
	mitsuba_mat_roughmetal, mitsuba_mat_dielectric, \
	mitsuba_mat_mirror, mitsuba_mat_difftrans, \
	mitsuba_mat_composite, mitsuba_emission
from mitsuba.operators import MITSUBA_OT_preset_engine_add, EXPORT_OT_mitsuba
from mitsuba.outputs import MtsLog, MtsFilmDisplay
from mitsuba.export.adjustments import MtsAdjustments
from mitsuba.export import translate_id 
from mitsuba.export.film import resolution
from mitsuba.export import get_instance_materials
from mitsuba.ui import render_panels, lamps
from mitsuba.ui.textures import TEXTURE_PT_context_texture_mts
from mitsuba.ui.textures import main, ldrtexture, checkerboard, \
		gridtexture, mapping
from mitsuba.ui.materials import MATERIAL_PT_context_material_mts
from mitsuba.ui.materials import main, lambertian, phong, ward, \
		microfacet, roughglass, roughmetal, dielectric, \
		mirror, difftrans, composite, emission

def compatible(mod):
	mod = __import__(mod)
	for subclass in mod.__dict__.values():
		try:
			subclass.COMPAT_ENGINES.add('mitsuba')
		except:
			pass
	del mod

import properties_data_lamp
properties_data_lamp.DATA_PT_context_lamp.COMPAT_ENGINES.add('mitsuba')
del properties_data_lamp

import properties_render
properties_render.RENDER_PT_render.COMPAT_ENGINES.add('mitsuba')
properties_render.RENDER_PT_dimensions.COMPAT_ENGINES.add('mitsuba')
properties_render.RENDER_PT_output.COMPAT_ENGINES.add('mitsuba')
del properties_render

compatible("properties_data_mesh")
compatible("properties_data_camera")

class RENDERENGINE_mitsuba(bpy.types.RenderEngine, engine_base):
	bl_idname			= 'mitsuba'
	bl_label			= 'Mitsuba'
	bl_use_preview      = True

	property_groups = [
		('Scene', mitsuba_engine),
		('Scene', mitsuba_integrator),
		('Scene', mitsuba_sampler),
		('Lamp', mitsuba_lamp),
		('Texture', mitsuba_texture),
		('mitsuba_texture', mitsuba_tex_ldrtexture),
		('mitsuba_texture', mitsuba_tex_checkerboard),
		('mitsuba_texture', mitsuba_tex_gridtexture),
		('mitsuba_texture', mitsuba_tex_mapping),
		('Material', mitsuba_material),
		('Material', mitsuba_emission),
		('mitsuba_material', mitsuba_mat_lambertian),
		('mitsuba_material', mitsuba_mat_phong),
		('mitsuba_material', mitsuba_mat_ward),
		('mitsuba_material', mitsuba_mat_microfacet),
		('mitsuba_material', mitsuba_mat_roughglass),
		('mitsuba_material', mitsuba_mat_roughmetal),
		('mitsuba_material', mitsuba_mat_dielectric),
		('mitsuba_material', mitsuba_mat_difftrans),
		('mitsuba_material', mitsuba_mat_mirror),
		('mitsuba_material', mitsuba_mat_composite)
	]

	render_lock = threading.Lock()

	def process_wait_timer(self):
		# Nothing to do here
		pass
	
	def render_preview(self, scene):
		# Iterate through the preview scene, finding objects with materials attached
		objects_materials = {}
						
		if resolution(scene) == (96, 96):
			return

		for object in [ob for ob in scene.objects if ob.is_visible(scene) and not ob.hide_render]:
			for mat in get_instance_materials(object):
				if mat is not None:
					if not object.name in objects_materials.keys(): objects_materials[object] = []
					objects_materials[object].append(mat)

		# find objects that are likely to be the preview objects
		preview_objects = [o for o in objects_materials.keys() if o.name.startswith('preview')]
		if len(preview_objects) < 1:
			return

		# find the materials attached to the likely preview object
		likely_materials = objects_materials[preview_objects[0]]
		if len(likely_materials) < 1:
			return

		tempdir = efutil.temp_directory()
		matfile = os.path.join(tempdir, "matpreview_materials.xml")
		output_file = os.path.join(tempdir, "matpreview.png")
		scene_file = os.path.join(os.path.join(plugin_path(),
			"matpreview"), "matpreview.xml")
		pm = likely_materials[0]
		adj = MtsAdjustments(matfile, tempdir, 
			bpy.data.materials, bpy.data.textures)
		adj.writeHeader()
		adj.exportMaterial(pm)
		adj.exportPreviewMesh(pm)
		adj.writeFooter()
		mts_path = scene.mitsuba_engine.binary_path
		mitsuba_binary = os.path.join(mts_path, "mitsuba")
		env = copy.copy(os.environ)
		mts_render_libpath = os.path.join(mts_path, "src/librender")
		mts_core_libpath = os.path.join(mts_path, "src/libcore")
		mts_hw_libpath = os.path.join(mts_path, "src/libhw")
		mts_bidir_libpath = os.path.join(mts_path, "src/libbidir")
		env['LD_LIBRARY_PATH'] = mts_core_libpath + ":" + mts_render_libpath + ":" + mts_hw_libpath + ":" + mts_bidir_libpath
		(width, height) = resolution(scene)
		refresh_interval = 1
		preview_spp = int(efutil.find_config_value('mitsuba', 'defaults', 'preview_spp', '16'))
		preview_depth = int(efutil.find_config_value('mitsuba', 'defaults', 'preview_depth', '2'))
		mitsuba_process = subprocess.Popen(
			[mitsuba_binary, '-q', 
				'-r%i' % refresh_interval,
				'-o', output_file, '-Dmatfile=%s' % matfile,
				'-Dwidth=%i' % width, 
				'-Dheight=%i' % height, 
				'-Dspp=%i' % preview_spp,
				'-Ddepth=%i' % preview_depth,
				'-o', output_file, scene_file],
			env = env,
			cwd = mts_path
		)
		framebuffer_thread = MtsFilmDisplay({
			'resolution': resolution(scene),
			'RE': self,
			'output_file': output_file
		})
		framebuffer_thread.set_kick_period(refresh_interval)
		framebuffer_thread.start()
		render_update_timer = None
		while mitsuba_process.poll() == None and not self.test_break():
			render_update_timer = threading.Timer(1, self.process_wait_timer)
			render_update_timer.start()
			if render_update_timer.isAlive(): render_update_timer.join()

		# If we exit the wait loop (user cancelled) and mitsuba is still running, then send SIGINT
		if mitsuba_process.poll() == None:
			# Use SIGTERM because that's the only one supported on Windows
			mitsuba_process.send_signal(subprocess.signal.SIGTERM)

		# Stop updating the render result and load the final image
		framebuffer_thread.stop()
		framebuffer_thread.join()

		if mitsuba_process.poll() != None and mitsuba_process.returncode != 0:
			MtsLog("MtsBlend: Rendering failed -- check the console")
		else:
			framebuffer_thread.kick(render_end=True)


	def render(self, scene):
		if scene is None:
			bpy.ops.ef.msg(msg_type='ERROR', msg_text='Scene to render is not valid')
			return
		if scene.mitsuba_engine.binary_path == '':
			bpy.ops.ef.msg(msg_type='ERROR', msg_text='The Mitsuba binary path is unspecified!')
			return

		with self.render_lock:	# just render one thing at a time
			if scene.name == 'preview':
				self.render_preview(scene)
				return

			scene_path = efutil.filesystem_path(scene.render.filepath)
			if os.path.isdir(scene_path):
				output_dir = scene_path
			else:
				output_dir = os.path.dirname(scene_path)		
			if output_dir[-1] != '/':
				output_dir += '/'
			efutil.export_path = output_dir
			os.chdir(output_dir)

			if scene.render.use_color_management == False:
				MtsLog('WARNING: Colour Management is switched off, render results may look too dark.')

			MtsLog('MtsBlend: Current directory = "%s"' % output_dir)
			output_basename = efutil.scene_filename() + '.%s.%05i' % (scene.name, scene.frame_current)

			export_result = bpy.ops.export.mitsuba(
				directory = output_dir,
				filename = output_basename,
				scene = scene.name
			)
			if 'CANCELLED' in export_result:
				bpy.ops.ef.msg(msg_type='ERROR', msg_text='Error while exporting -- check the console for details.')
				return 

			if scene.mitsuba_engine.export_mode == 'render':
				mts_path = scene.mitsuba_engine.binary_path
				mtsgui_binary = os.path.join(mts_path, "mtsgui")
				mitsuba_binary = os.path.join(mts_path, "mitsuba")
				env = copy.copy(os.environ)
				mts_render_libpath = os.path.join(mts_path, "src/librender")
				mts_core_libpath = os.path.join(mts_path, "src/libcore")
				mts_hw_libpath = os.path.join(mts_path, "src/libhw")
				mts_bidir_libpath = os.path.join(mts_path, "src/libbidir")
				env['LD_LIBRARY_PATH'] = mts_core_libpath + ":" + mts_render_libpath + ":" + mts_hw_libpath + ":" + mts_bidir_libpath

				MtsLog("MtsBlend: Launching renderer ..")
				if scene.mitsuba_engine.render_mode == 'gui':
					subprocess.Popen(
						[mtsgui_binary, efutil.export_path],
						env = env,
						cwd = mts_path
					)
				elif scene.mitsuba_engine.render_mode == 'cli':
					output_file = efutil.export_path[:-4] + ".png"
					mitsuba_process = subprocess.Popen(
						[mitsuba_binary, '-r',  '%d' % scene.mitsuba_engine.refresh_interval,
							'-o', output_file, efutil.export_path],
						env = env,
						cwd = mts_path
					)
					framebuffer_thread = MtsFilmDisplay({
						'resolution': resolution(scene),
						'RE': self,
						'output_file': output_file
					})
					framebuffer_thread.set_kick_period(scene.mitsuba_engine.refresh_interval) 
					framebuffer_thread.start()
					render_update_timer = None
					while mitsuba_process.poll() == None and not self.test_break():
						render_update_timer = threading.Timer(1, self.process_wait_timer)
						render_update_timer.start()
						if render_update_timer.isAlive(): render_update_timer.join()

					# If we exit the wait loop (user cancelled) and mitsuba is still running, then send SIGINT
					if mitsuba_process.poll() == None:
						# Use SIGTERM because that's the only one supported on Windows
						mitsuba_process.send_signal(subprocess.signal.SIGTERM)

					# Stop updating the render result and load the final image
					framebuffer_thread.stop()
					framebuffer_thread.join()

					if mitsuba_process.poll() != None and mitsuba_process.returncode != 0:
						MtsLog("MtsBlend: Rendering failed -- check the console")
						bpy.ops.ef.msg(msg_type='ERROR', msg_text='Rendering failed -- check the console.')
					else:
						framebuffer_thread.kick(render_end=True)