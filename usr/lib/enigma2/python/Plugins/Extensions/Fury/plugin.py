# -*- coding: utf-8 -*-
# Skin Fury by islam salama (( Abou Yassin ))
# yassin.s76m@gmail.com
# mod by Lululla
from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.Standby import TryQuitMainloop
from Components.About import about
from Components.ActionMap import ActionMap
from Components.config import (
    config, configfile, ConfigYesNo, ConfigSubsection, getConfigListEntry,
    ConfigSelection, ConfigNumber, ConfigText, ConfigInteger, NoSave, ConfigNothing
)
from Components.ConfigList import ConfigListScreen
from Components.Sources.Progress import Progress
from Tools.Downloader import downloadWithProgress
from Components.Sources.StaticText import StaticText
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.AVSwitch import AVSwitch
from Tools.Directories import fileExists
import os
import sys
import re
from enigma import ePicLoad

PY3 = sys.version_info.major >= 3
if PY3:
    from urllib.request import urlopen, Request
else:
    from urllib2 import urlopen, Request

# ------------------- Plugin Verioin -------------------

version = "6.6"


# --- Helpers: version compare (avoid string-compare pitfalls) ---
def _ver_tuple(v):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", str(v)))
    except Exception:
        return (0,)

# ------------------- Fury Config -------------------
config.plugins.Fury = ConfigSubsection()
config.plugins.Fury.skinSelector = ConfigSelection(default='base', choices=[
 ('base', _('Default'))])
config.plugins.Fury.colorSelector = ConfigSelection(default='head', choices=[
 ('head', _('Default')),
 ('color1_Red', _('Red')),
 ('color2_Green', _('Green')),
 ('color3_Grey', _('Grey')),
 ('color4_Blue', _('Blue')),
 ('color5_Green2', _('Green2')),
 ('color6_Brown', _('Brown')),
 ('color7_Dark Purple', _('Dark Purple')),
 ('color8_Dark Red', _('Dark Red')),
 ('color9_Orange', _('Orange')),
 ('color10_Grey2', _('Grey2')),
 ('color11_Oily', _('Oily')),
 ('color12_Black', _('Black'))])
config.plugins.Fury.FontStyle = ConfigSelection(default='basic', choices=[
 ('basic', _('Default')),
 ('font1', _('Verdana')),
 ('font2', _('Cravelo')),
 ('font3', _('Mongule')),
 ('font4', _('Tenada')),
 ('font5', _('HandelGotDBol')),
 ('font6', _('Fury')),
 ('font7', _('Joyful')),
 ('font8', _('Nexa')),
 ('font9', _('Tommy ')),
 ('font10', _('Poetsen_One '))])
config.plugins.Fury.FontScale = ConfigSelection(default='93', choices=[
 ('93', _('Default')),
 ('75',  _('-25%')),
 ('80',  _('-20%')),
 ('85',  _('-15%')),
 ('90',  _('-10%')),
 ('95',  _('-5%')),
 ('102', _('+2%')),
 ('105', _('+5%')),
 ('107', _('+7%')),
 ('109', _('+9%')),
 ('110', _('+10%')),
 ('113', _('+13%')), 
 ('115', _('+15%')),
 ('120', _('+20%')), 
 ('125', _('+25%')),
 ('130', _('+30%'))]) 
config.plugins.Fury.transparency = ConfigSelection(default='06', choices=[
 ('06', _('Default')),
 ('10', _('~5%')),
 ('20', _('~10%')),
 ('25', _('~15%')),
 ('30', _('~20%')),
 ('40', _('~25%')),
 ('55', _('~30%')),
 ('65', _('~35%')),
 ('75', _('~40%')),
 ('80', _('~45%')),
 ('90', _('~50%')),
 ('00', _('Zero'))])

config.plugins.Fury.InfobarStyle = ConfigSelection(default='infobar_no_posters', choices=[
 ('infobar_no_posters', _('Infobar_NO_Posters')),
 ('infobar_2_posters', _('Infobar_2_Posters')),
 ('infobar_3_posters', _('Infobar_3_Posters')),
 ('infobar_mini', _('Infobar_Mini')),
 ('infobar_mini_2', _('Infobar_Mini_2')),
 ('infobar_mini_2_logo', _('Infobar_Mini_2_Logo')),
 ('infobar_round', _('Infobar_Round')),
 ('infobar_2_round', _('Infobar_2_Round')),
 ('infobar2_no_posters', _('Infobar2_NO_Posters')),
 ('infobar2_2_posters', _('Infobar2_2_Posters')),
 ('infobar2_2_logo', _('Infobar2_2_Logo')),
 ('infobar2_3_posters', _('Infobar2_3_Posters')),
 ('infobar2_3_backdrop', _('Infobar2_3_Backdrop')),
 ('infobar3_2_posters', _('Infobar3_2_Posters')),
 ('infobar3_2_logo', _('Infobar3_2_Logo')),
 ('infobar3_2_backdrop', _('Infobar3_2_Backdrop')),
 ('infobar3_3_posters', _('Infobar3_3_Posters')),
 ('infobar3_no_posters', _('Infobar3_NO_Posters')),
 ('infobar3_no_posters_black', _('Infobar3_NO_Posters_Black')),
 ('infobar3_2_posters_black', _('Infobar3_2_Posters_Black')),
 ('infobar3_3_posters_black', _('Infobar3_3_Posters_Black')),
 ('infobar3_2_backdrop_black', _('Infobar3_2_Backdrop_Black')),
 ('infobar4_no_posters', _('Infobar4_NO_Posters')),
 ('infobar4_1_posters', _('Infobar4_1_Posters')),
 ('infobar4_3_posters', _('Infobar4_3_Posters')),
 ('infobar4_backdrop', _('Infobar4_BackDrop')),
 ('infobar4_1_logo', _('Infobar4_1_Logo')),
 ('infobar5_no_posters', _('Infobar5_NO_Posters')),
 ('infobar5_2_posters', _('Infobar5_2_Posters')),
 ('infobar5_2_logo', _('Infobar5_2_Logo')),
 ('infobar5_3_posters', _('Infobar5_3_Posters')),
 ('infobar5_star_3_backdrop', _('Infobar5_star_3_Backdrop')),
 ('infobar5_star_3_posters', _('Infobar5_star_3_Posters'))])
config.plugins.Fury.SecondInfobarStyle = ConfigSelection(default='secondinfobar_no_posters', choices=[
 ('secondinfobar_no_posters', _('SecondInfobar_NO_Posters')),
 ('secondinfobar_posters', _('SecondInfobar_Posters')),
 ('secondinfobar2_no_posters', _('SecondInfobar2_NO_Posters')),
 ('secondinfobar2_posters', _('SecondInfobar2_Posters')),
 ('secondinfobar3_no_posters', _('SecondInfobar3_NO_Posters')),
 ('secondinfobar3_posters', _('SecondInfobar3_Posters')),
 ('secondinfobar4_no_posters', _('SecondInfobar4_NO_Posters')),
 ('secondinfobar4_posters', _('SecondInfobar4_Posters')),
 ('secondinfobar5_signal', _('SecondInfobar5_Signal'))])
config.plugins.Fury.ChannSelector = ConfigSelection(default='channellist_no_posters', choices=[
 ('channellist_no_posters', _('ChannelSelection_NO_Posters')),
 ('channellist_5_posters', _('ChannelSelection_5_Posters')),
 ('channellist_5_backdrop', _('ChannelSelection_5_Backdrop')),
 ('channellist2_no_posters', _('ChannelSelection2_NO_Posters')),
 ('channellist2_1_posters', _('ChannelSelection2_1_Posters')),
 ('channellist2_4_posters', _('ChannelSelection2_4_Posters')),
 ('channellist2_6_posters', _('ChannelSelection2_6_Posters')),
 ('channellist2_12_posters', _('ChannelSelection2_12_Posters'))])
config.plugins.Fury.EventView = ConfigSelection(default='eventview_no_posters', choices=[
 ('eventview_no_posters', _('EventView_NO_Posters')),
 ('eventview_7_posters', _('EventView_7_Posters')),
 ('eventview_11_posters', _('EventView_11_Posters')),
 ('eventview_12_posters', _('EventView_12_Posters'))])
config.plugins.Fury.EPGSelection = ConfigSelection(default='epgselection_no_posters', choices=[
 ('epgselection_no_posters', _('EPGSelection_No_Posters')),
 ('epgselection_1_posters', _('EPGSelection_1_Posters'))])
config.plugins.Fury.VolumeBar = ConfigSelection(default='volume', choices=[
 ('volume', _('Default')),
 ('volume2', _('volume2')),
 ('volume3', _('volume3')),
 ('volume4', _('volume4'))])
config.plugins.Fury.ChannForegroundColor = ConfigSelection(default='#ffffff', choices=[
 ('#ffffff', _('Default'))])
config.plugins.Fury.ChannForegroundColorSelected = ConfigSelection(default='white', choices=[
 ('#ffffff', _('Default')),
 ('#e7e412', _('Yellow')),
 ('#FFFAFA', _('LightWhite')),
 ('#ff4a3c', _('Red')),
 ('#22539e', _('Blue')),
 ('#32CD32', _('Green')),
 ('#fcc000', _('Orange')),
 ('#00FFFF', _('Cyan')),
 ('#E799A3', _('Pink'))])
config.plugins.Fury.ChannServiceDescriptionColor = ConfigSelection(default='white', choices=[
 ('#49bbff', _('Default')),
 ('#e7e412', _('Yellow')),
 ('#FFFAFA', _('LightWhite')),
 ('#ff4a3c', _('Red')),
 ('#22539e', _('Blue')),
 ('#32CD32', _('Green')),
 ('#fcc000', _('Orange')),
 ('#00FFFF', _('Cyan')),
 ('#E799A3', _('Pink'))])
config.plugins.Fury.ChannServiceDescriptionColorSelected = ConfigSelection(default='white', choices=[
 ('#a1daff', _('Default')),
 ('#e7e412', _('Yellow')),
 ('#FFFAFA', _('LightWhite')),
 ('#FF4A4A', _('Red')),
 ('#6BA6FF', _('Blue')),
 ('#32CD32', _('Green')),
 ('#FFAE08', _('Orange')),
 ('#00CCCC', _('Cyan')),
 ('#FF8F9C', _('Pink'))])
config.plugins.Fury.ChannBackgroundColorSelected = ConfigSelection(default='#1b3c85', choices=[
 ('#1b3c85', _('Default')),
 ('#151311', _('Black')),
 ('#800000', _('Red')),
 ('#275918', _('Green')),
 ('#43494D', _('Grey')),
 ('#013C66', _('Blue')),
 ('#1b5c53', _('Green2')),
 ('#613f3f', _('Brown')),
 ('#5e1a67', _('DarkPurple')),
 ('#800001', _('DarkRed')),
 ('#151312', _('Orange')),
 ('#793604', _('Oily'))])

# ------------------- Plugin Entry -------------------
def Plugins(**kwargs):
    return PluginDescriptor(
        name='Fury v.%s' % version,
        description=_('Fury-FHD-CBL Skin Mod AbouYassin7'),
        where=PluginDescriptor.WHERE_PLUGINMENU,
        icon='plugin.png',
        fnc=main
    )

def main(session, **kwargs):
    session.open(FurySetup)


# --- Helpers: font scale + header-only transparency ---
def _apply_scale_to_font_xml(xml_text, scale_value):
    import re
    xml_text = re.sub(r'scale="\d+"', f'scale="{scale_value}"', xml_text)
    def _inject_scale(m):
        tag = m.group(0)
        return tag if 'scale=' in tag else tag[:-2] + f' scale="{scale_value}" />'
    xml_text = re.sub(r'<font\s+name="Regular"[^>]*?/>', _inject_scale, xml_text)
    return xml_text

def _apply_transparency_to_header_color(xml_text, alpha_hex):
    import re
    if not re.match(r'^[0-9A-Fa-f]{2}$', (alpha_hex or '')):
        return xml_text
    # ONLY change the header color node
    def _repl(m):
        return f"{m.group(1)}{alpha_hex.upper()}{m.group(2)}{m.group(3)}"
    xml_text = re.sub(
        r'(<color\s+name="header"[^>]*?value="#)[0-9A-Fa-f]{2}([0-9A-Fa-f]{6})(")',
        _repl, xml_text, flags=re.IGNORECASE
    )
    return xml_text

class FurySetup(ConfigListScreen, Screen):
    skin = '<screen name="FurySetup" position="center,center" size="1000,640" title="Fury-FHD-CBL Plugin">\n\t\t  <eLabel font="Regular; 24" foregroundColor="#00ff4A3C" halign="center" position="20,598" size="120,26" text="Cancel" />\n\t\t  <eLabel font="Regular; 24" foregroundColor="#0056C856" halign="center" position="220,598" size="120,26" text="Save" />\n\t\t  <widget name="Preview" position="997,690" size="498, 280" zPosition="1" />\n\t\t <widget name="config" font="Regular; 24" itemHeight="40" position="5,5" scrollbarMode="showOnDemand" size="990,550" />\n\t\t\n\t\t  </screen>'

    def __init__(self, session):
        self.version = '.Fury-FHD-CBL'
        Screen.__init__(self, session)
        try:
            self.setTitle('Fury-FHD-CBL Plugin v.%s' % version)
        except Exception:
            pass
        self.session = session
        self.skinFile = '/usr/share/enigma2/Fury-FHD-CBL/skin.xml'
        self.previewFiles = '/usr/lib/enigma2/python/Plugins/Extensions/Fury/sample/'
        self['Preview'] = Pixmap()
        list = []
        list.append(getConfigListEntry(_('Color Style:'), config.plugins.Fury.colorSelector))
        list.append(getConfigListEntry(_('Skin Style:'), config.plugins.Fury.skinSelector))
        list.append(getConfigListEntry(_('Select Your Font:'), config.plugins.Fury.FontStyle))
        list.append(getConfigListEntry(_('Font Size:'), config.plugins.Fury.FontScale))
        list.append(getConfigListEntry(_('Transparency:'), config.plugins.Fury.transparency))
        list.append(getConfigListEntry(_('InfoBar Style:'), config.plugins.Fury.InfobarStyle))
        list.append(getConfigListEntry(_('SecondInfobar Style:'), config.plugins.Fury.SecondInfobarStyle))
        list.append(getConfigListEntry(_('ChannelSelection Style:'), config.plugins.Fury.ChannSelector))
        list.append(getConfigListEntry(_('EventView Style:'), config.plugins.Fury.EventView))
        list.append(getConfigListEntry(_('EPGSelection Style:'), config.plugins.Fury.EPGSelection))
        list.append(getConfigListEntry(_('VolumeBar Style:'), config.plugins.Fury.VolumeBar))
        list.append(getConfigListEntry(_('Channel Foreground Color:'), config.plugins.Fury.ChannForegroundColor))
        list.append(getConfigListEntry(_('Channel Selected Foreground Color:'), config.plugins.Fury.ChannForegroundColorSelected))
        list.append(getConfigListEntry(_('Channel Description Color:'), config.plugins.Fury.ChannServiceDescriptionColor))
        list.append(getConfigListEntry(_('Channel Selected Description Color:'), config.plugins.Fury.ChannServiceDescriptionColorSelected))
        list.append(getConfigListEntry(_('Channel Background Selected Color:'), config.plugins.Fury.ChannBackgroundColorSelected))


        ConfigListScreen.__init__(self, list)
        self['actions'] = ActionMap(
            ['OkCancelActions','DirectionActions','InputActions','ColorActions'], 
            {'left': self.keyLeft,
            'down': self.keyDown,
            'up': self.keyUp,
            'right': self.keyRight,
            'red': self.keyExit,
            'green': self.keySave,
            'yellow': self.checkforUpdate,
            'blue': self.info,
            'cancel': self.keyExit}, -1)
        self.onLayoutFinish.append(self.UpdateComponents)
        self.PicLoad = ePicLoad()
        self.Scale = AVSwitch().getFramebufferScale()
        try:
            self.PicLoad.PictureData.get().append(self.DecodePicture)
        except:
            self.PicLoad_conn = self.PicLoad.PictureData.connect(self.DecodePicture)

    def modify_channel_colors(self, content):
       fg_color = config.plugins.Fury.ChannForegroundColor.value
       fg_selected_color = config.plugins.Fury.ChannForegroundColorSelected.value
       desc_color = config.plugins.Fury.ChannServiceDescriptionColor.value
       desc_selected_color = config.plugins.Fury.ChannServiceDescriptionColorSelected.value
       bg_selected_color = config.plugins.Fury.ChannBackgroundColorSelected.value

       content = content.replace('foregroundColor="white"', f'foregroundColor="{fg_color}"')
       content = content.replace('foregroundColorSelected="#ffffff"', f'foregroundColorSelected="{fg_selected_color}"')
       content = content.replace('colorServiceDescription="#49bbff"', f'colorServiceDescription="{desc_color}"')
       content = content.replace('colorServiceDescriptionSelected="#a1daff"', f'colorServiceDescriptionSelected="{desc_selected_color}"')
       content = content.replace('backgroundColorSelected="bluette"', f'backgroundColorSelected="{bg_selected_color}"')
       
       return content

    def keySave(self):
        if not fileExists(self.skinFile + self.version):
            for x in self['config'].list:
                x[1].cancel()
            self.close()
            return
        for x in self['config'].list:
            x[1].save()
        try:
            skin_lines = []
            head_file = self.previewFiles + 'head-' + config.plugins.Fury.colorSelector.value + '.xml'
            with open(head_file, 'r') as skFile:
                _head = skFile.read()
            _head = _apply_transparency_to_header_color(_head, config.plugins.Fury.transparency.value)
            skin_lines.append(_head)
            font_file = self.previewFiles + 'font-' + config.plugins.Fury.FontStyle.value + '.xml'
            with open(font_file, 'r') as skFile:
                _font = skFile.read()
            _font = _apply_scale_to_font_xml(_font, config.plugins.Fury.FontScale.value)
            skin_lines.append(_font)
            skn_file = self.previewFiles + 'infobar-' + config.plugins.Fury.InfobarStyle.value + '.xml'
            with open(skn_file, 'r') as skFile:
                skin_lines.extend(skFile.readlines())
            skn_file = self.previewFiles + 'secondinfobar-' + config.plugins.Fury.SecondInfobarStyle.value + '.xml'
            with open(skn_file, 'r') as skFile:
                skin_lines.extend(skFile.readlines())
            # هنا تعديل القنوات
            skn_file = self.previewFiles + 'channellist-' + config.plugins.Fury.ChannSelector.value + '.xml'
            with open(skn_file, 'r') as f:
                channellist_content = f.read()
            channellist_content = self.modify_channel_colors(channellist_content)
            skin_lines.append(channellist_content)
            # باقي الملفات
            skn_file = self.previewFiles + 'eventview-' + config.plugins.Fury.EventView.value + '.xml'
            with open(skn_file, 'r') as skFile:
                skin_lines.extend(skFile.readlines())
            skn_file = self.previewFiles + 'epgselection-' + config.plugins.Fury.EPGSelection.value + '.xml'
            with open(skn_file, 'r') as skFile:
                skin_lines.extend(skFile.readlines())
            skn_file = self.previewFiles + 'vol-' + config.plugins.Fury.VolumeBar.value + '.xml'
            with open(skn_file, 'r') as skFile:
                skin_lines.extend(skFile.readlines())
            base_file = self.previewFiles + 'base.xml'
            if config.plugins.Fury.skinSelector.value == 'base1':
                base_file = self.previewFiles + 'base1.xml'
            if config.plugins.Fury.skinSelector.value == 'base':
                base_file = self.previewFiles + 'base.xml'
            with open(base_file, 'r') as skFile:
                skin_lines.extend(skFile.readlines())
            with open(self.skinFile, 'w') as xFile:
                xFile.writelines(skin_lines)
        except:
            self.session.open(MessageBox, _('Error by processing the skin file !!!'), MessageBox.TYPE_ERROR)
        restartbox = self.session.openWithCallback(
            self.restartGUI, MessageBox,
            _('GUI needs a restart to apply a new skin.\nDo you want to Restart the GUI now?'),
            MessageBox.TYPE_YESNO)
        restartbox.setTitle(_('Restart GUI now?'))

    def GetPicturePath(self):
        try:
            returnValue = self['config'].getCurrent()[1].value
            path = '/usr/lib/enigma2/python/Plugins/Extensions/Fury/screens/' + returnValue + '.png'
            if fileExists(path):
                return path
            else:
                return '/usr/lib/enigma2/python/Plugins/Extensions/Fury/screens/default.png'
        except:
            return '/usr/lib/enigma2/python/Plugins/Extensions/Fury/screens/default.png'

    def UpdatePicture(self):
        self.PicLoad.PictureData.get().append(self.DecodePicture)
        self.onLayoutFinish.append(self.ShowPicture)

    def ShowPicture(self, data=None):
        if self["Preview"].instance:
            width = 450
            height = 250
            self.PicLoad.setPara([width, height, self.Scale[0], self.Scale[1], 0, 1, "ff000000"])
            if self.PicLoad.startDecode(self.GetPicturePath()):
                self.PicLoad = ePicLoad()
                try:
                    self.PicLoad.PictureData.get().append(self.DecodePicture)
                except:
                    self.PicLoad_conn = self.PicLoad.PictureData.connect(self.DecodePicture)

    def DecodePicture(self, PicInfo=None):
        ptr = self.PicLoad.getData()
        if ptr is not None:
            self["Preview"].instance.setPixmap(ptr)
            self["Preview"].instance
            self["Preview"].instance.show()
        return

    def UpdateComponents(self):
        self.UpdatePicture()

    def info(self):
        aboutbox = self.session.open(MessageBox, _('Setup Fury for Fury-FHD-CBL v.%s') % version, MessageBox.TYPE_INFO)
        aboutbox.setTitle(_('Info...'))

    def keyLeft(self):
        ConfigListScreen.keyLeft(self)
        self.ShowPicture()

    def keyRight(self):
        ConfigListScreen.keyRight(self)
        self.ShowPicture()

    def keyDown(self):
        self['config'].instance.moveSelection(self['config'].instance.moveDown)
        self.ShowPicture()

    def keyUp(self):
        self['config'].instance.moveSelection(self['config'].instance.moveUp)
        self.ShowPicture()

    def restartGUI(self, answer):
        if answer is True:
            self.session.open(TryQuitMainloop, 3)
        else:
            self.close()

    def checkforUpdate(self):
        try:
            fp = ''
            destr = '/tmp/furyversion.txt'
            req = Request('https://raw.githubusercontent.com/islam-2412/IPKS/main/fury/furyversion.txt')
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
            fp = urlopen(req)
            fp = fp.read().decode('utf-8')
            print('fp read:', fp)
            with open(destr, 'w') as f:
                f.write(str(fp))
                f.seek(0)
                f.close()
            if os.path.exists(destr):
                with open(destr, 'r') as cc:
                    s1 = cc.readline()
                    vers = s1.split('#')[0]
                    url = s1.split('#')[1]
                    version_server = vers.strip()
                    self.updateurl = url.strip()
                    cc.close()
                    if _ver_tuple(version_server) == _ver_tuple(version):
                        message = '%s %s\n%s %s\n\n%s' % (_('Server version:'),
                         version_server,
                         _('Version installed:'),
                         version,
                         _('You have the current version Fury!'))
                        self.session.open(MessageBox, message, MessageBox.TYPE_INFO)
                    elif _ver_tuple(version_server) > _ver_tuple(version):
                        message = '%s %s\n%s %s\n\n%s' % (_('Server version:'),
                         version_server,
                         _('Version installed:'),
                         version,
                         _('The update is available!\n\nDo you want to run the update now?'))
                        self.session.openWithCallback(self.update, MessageBox, message, MessageBox.TYPE_YESNO)
                    else:
                        self.session.open(MessageBox, _('You have version %s!!!') % version, MessageBox.TYPE_ERROR)
        except Exception as e:
            print('error: ', str(e))

    def update(self, answer):
        if answer is True:
            self.session.open(FuryUpdater, self.updateurl)
        else:
            return

    def keyExit(self):
        for x in self['config'].list:
            x[1].cancel()
        self.close()


class FuryUpdater(Screen):

    def __init__(self, session, updateurl):
        self.session = session
        skin = '''
                <screen name="FuryUpdater" position="center,center" size="840,260" flags="wfBorder" backgroundColor="background">
                    <widget name="status" position="20,10" size="800,70" transparent="1" font="Regular; 40" foregroundColor="foreground" backgroundColor="background" valign="center" halign="center" noWrap="1" zPosition="1" />
                    <widget source="progress" render="Progress" position="20,120" size="800,20" transparent="1" borderWidth="0" foregroundColor="ltbluette" backgroundColor="background" />
                    <widget source="progresstext" render="Label" position="209,164" zPosition="2" font="Regular; 28" halign="center" transparent="1" size="400,70" foregroundColor="foreground" backgroundColor="background" />
                    <ePixmap position="62,20" size="90,40" pixmap="buttons/F-30.png" alphatest="blend" zPosition="4" />
                </screen>                
                '''
        self.skin = skin
        Screen.__init__(self, session)
        try:
            self.setTitle('Fury Updater v.%s' % version)
        except Exception:
            pass

        self.updateurl = updateurl
        print('self.updateurl', self.updateurl)
        self['status'] = Label()
        self['progress'] = Progress()
        self['progresstext'] = StaticText()
        self.icount = 0
        self.downloading = False
        self.last_recvbytes = 0
        self.error_message = None
        self.download = None
        self.aborted = False
        self.startUpdate()

    def startUpdate(self):
        self['status'].setText(_('Downloading Fury...'))
        self.dlfile = '/tmp/fury.ipk'
        print('self.dlfile', self.dlfile)
        self.download = downloadWithProgress(self.updateurl, self.dlfile)
        self.download.addProgress(self.downloadProgress)
        self.download.start().addCallback(self.downloadFinished).addErrback(self.downloadFailed)

    def downloadFinished(self, string=''):
        self['status'].setText(_('Installing updates!'))
        # Use force-reinstall so the update applies even if the IPK carries the same version
        # (common packaging mistake) and capture logs for troubleshooting.
        logf = '/tmp/fury_update.log'
        cmd = "opkg install --force-reinstall --force-overwrite /tmp/fury.ipk >%s 2>&1" % logf
        ret = os.system(cmd)
        os.system('sync')
        os.system('rm -f /tmp/fury.ipk')
        os.system('sync')
        if ret != 0:
            self['status'].setText(_('Install failed!'))
            self.session.open(MessageBox, _('Update failed. Please check %s') % logf, MessageBox.TYPE_ERROR)
            return
        restartbox = self.session.openWithCallback(self.restartGUI, MessageBox, _('Fury update was done!!!\nDo you want to restart the GUI now?'), MessageBox.TYPE_YESNO)
        restartbox.setTitle(_('Restart GUI now?'))

    def downloadFailed(self, failure_instance=None, error_message=''):
        text = _('Error downloading files!')
        if error_message == '' and failure_instance is not None:
            error_message = failure_instance.getErrorMessage()
            text += ': ' + error_message
        self['status'].setText(text)
        return

    def downloadProgress(self, recvbytes, totalbytes):
        self['status'].setText(_('Download in progress...'))
        self['progress'].value = int(100 * recvbytes / float(totalbytes))
        self['progresstext'].text = '%d of %d kBytes (%.2f%%)' % (recvbytes / 1024, totalbytes / 1024, 100 * recvbytes / float(totalbytes))
        self.last_recvbytes = recvbytes

    def restartGUI(self, answer):
        if answer is True:
            self.session.open(TryQuitMainloop, 3)
        else:
            self.close()
