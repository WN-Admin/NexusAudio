THEMES = {
    'Nexus Dark': {
        'dark': True,
        'colors': {
            'primary': '#0CA4FB', 'secondary': '#6DCEFE', 'accent': '#025E93',
            'background': '#012235', 'surface': '#011B2A',
            'text': '#FFFFFF', 'text_secondary': '#6DCEFE',
        }
    },
    'Nexus Light': {
        'dark': False,
        'colors': {
            'primary': '#0CA4FB', 'secondary': '#025E93', 'accent': '#6DCEFE',
            'background': '#F8FAFC', 'surface': '#FFFFFF',
            'text': '#012235', 'text_secondary': '#025E93',
        }
    },
    'Ocean': {
        'dark': True,
        'colors': {
            'primary': '#025E93', 'secondary': '#0CA4FB', 'accent': '#6DCEFE',
            'background': '#011B2A', 'surface': '#012235',
            'text': '#FFFFFF', 'text_secondary': '#6DCEFE',
        }
    },
    'Midnight': {
        'dark': True,
        'colors': {
            'primary': '#0CA4FB', 'secondary': '#6DCEFE', 'accent': '#025E93',
            'background': '#001122', 'surface': '#012235',
            'text': '#FFFFFF', 'text_secondary': '#6DCEFE',
        }
    },
    'Arctic': {
        'dark': False,
        'colors': {
            'primary': '#025E93', 'secondary': '#0CA4FB', 'accent': '#6DCEFE',
            'background': '#F0F8FF', 'surface': '#FFFFFF',
            'text': '#001122', 'text_secondary': '#025E93',
        }
    },
    'Dracula': {
        'dark': True,
        'colors': {
            'primary': '#BD93F9', 'secondary': '#FF79C6', 'accent': '#50FA7B',
            'background': '#282A36', 'surface': '#44475A',
            'text': '#F8F8F2', 'text_secondary': '#BD93F9',
        }
    },
    'Nord': {
        'dark': True,
        'colors': {
            'primary': '#88C0D0', 'secondary': '#81A1C1', 'accent': '#B48EAD',
            'background': '#2E3440', 'surface': '#3B4252',
            'text': '#ECEFF4', 'text_secondary': '#88C0D0',
        }
    },
    'Solarized Dark': {
        'dark': True,
        'colors': {
            'primary': '#268BD2', 'secondary': '#859900', 'accent': '#B58900',
            'background': '#002B36', 'surface': '#073642',
            'text': '#93A1A1', 'text_secondary': '#268BD2',
        }
    },
    'Monokai': {
        'dark': True,
        'colors': {
            'primary': '#A6E22E', 'secondary': '#F92672', 'accent': '#FD971F',
            'background': '#272822', 'surface': '#3E3D32',
            'text': '#F8F8F2', 'text_secondary': '#A6E22E',
        }
    },
    'Gruvbox Dark': {
        'dark': True,
        'colors': {
            'primary': '#D79921', 'secondary': '#98971A', 'accent': '#FB4934',
            'background': '#282828', 'surface': '#3C3836',
            'text': '#EBDBB2', 'text_secondary': '#D79921',
        }
    },
    'Catppuccin Mocha': {
        'dark': True,
        'colors': {
            'primary': '#89B4FA', 'secondary': '#F5C2E7', 'accent': '#A6E3A1',
            'background': '#1E1E2E', 'surface': '#313244',
            'text': '#CDD6F4', 'text_secondary': '#89B4FA',
        }
    },
    'Tokyo Night': {
        'dark': True,
        'colors': {
            'primary': '#7AA2F7', 'secondary': '#BB9AF7', 'accent': '#9ECE6A',
            'background': '#1A1B26', 'surface': '#24283B',
            'text': '#A9B1D6', 'text_secondary': '#7AA2F7',
        }
    },
    'Ayu Dark': {
        'dark': True,
        'colors': {
            'primary': '#FF9940', 'secondary': '#39BAE6', 'accent': '#D2A6FF',
            'background': '#0F1419', 'surface': '#1A1F29',
            'text': '#E6E1CF', 'text_secondary': '#FF9940',
        }
    },
    'Rose Pine': {
        'dark': True,
        'colors': {
            'primary': '#CA9EE6', 'secondary': '#F6C177', 'accent': '#3E8FB0',
            'background': '#191724', 'surface': '#1F1D2E',
            'text': '#E0DEF4', 'text_secondary': '#CA9EE6',
        }
    },
    'Everforest Dark': {
        'dark': True,
        'colors': {
            'primary': '#A7C080', 'secondary': '#D3C6AA', 'accent': '#E69875',
            'background': '#2D353B', 'surface': '#343F44',
            'text': '#D3C6AA', 'text_secondary': '#A7C080',
        }
    },
}


def get_stylesheet(theme_name='Nexus Dark'):
    theme = THEMES.get(theme_name, THEMES['Nexus Dark'])
    c = theme['colors']

    return f"""
    QMainWindow {{
        background-color: {c['background']};
        color: {c['text']};
    }}
    QTabWidget::pane {{
        border: 1px solid {c['primary']};
        background-color: {c['surface']};
    }}
    QTabBar::tab {{
        background-color: {c['surface']};
        color: {c['text']};
        padding: 8px 16px;
        margin: 2px;
        border-radius: 4px;
    }}
    QTabBar::tab:selected {{
        background-color: {c['primary']};
        color: white;
        font-weight: bold;
    }}
    QTabBar::tab:hover {{
        background-color: {c['secondary']};
        color: white;
    }}
    QPushButton {{
        background-color: {c['primary']};
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 4px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background-color: {c['secondary']};
    }}
    QPushButton:pressed {{
        background-color: {c['accent']};
    }}
    QPushButton:disabled {{
        background-color: #555555;
        color: #999999;
    }}
    QLineEdit, QTextEdit, QListWidget, QTableWidget {{
        background-color: {c['surface']};
        color: {c['text']};
        border: 1px solid {c['primary']};
        border-radius: 4px;
        padding: 4px;
    }}
    QComboBox {{
        background-color: {c['surface']};
        color: {c['text']};
        border: 1px solid {c['primary']};
        border-radius: 4px;
        padding: 4px;
    }}
    QComboBox::drop-down {{
        background-color: {c['primary']};
        border-radius: 0 4px 4px 0;
    }}
    QComboBox QAbstractItemView {{
        background-color: {c['surface']};
        color: {c['text']};
        selection-background-color: {c['primary']};
        selection-color: white;
        border: 1px solid {c['primary']};
        outline: none;
    }}
    QTableWidget::item:selected {{
        background-color: {c['primary']};
        color: white;
    }}
    QHeaderView::section {{
        background-color: {c['primary']};
        color: white;
        padding: 4px;
        border: none;
    }}
    QLabel {{
        color: {c['text']};
    }}
    QProgressBar {{
        border: 1px solid {c['primary']};
        border-radius: 4px;
        background-color: {c['surface']};
        text-align: center;
    }}
    QProgressBar::chunk {{
        background-color: {c['primary']};
        border-radius: 3px;
    }}
    QCheckBox {{
        color: {c['text']};
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
    }}
    QScrollBar:vertical {{
        background-color: {c['surface']};
        width: 12px;
    }}
    QScrollBar::handle:vertical {{
        background-color: {c['primary']};
        border-radius: 6px;
    }}
    QGroupBox {{
        border: 1px solid {c['primary']};
        border-radius: 4px;
        margin-top: 10px;
        padding: 8px;
        font-weight: bold;
        color: {c['text']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }}
    QDialog {{
        background-color: {c['background']};
        color: {c['text']};
    }}
    QDialog QListWidget {{
        background-color: {c['surface']};
        color: {c['text']};
        border: 1px solid {c['primary']};
    }}
    """


def get_theme_names():
    return list(THEMES.keys())
