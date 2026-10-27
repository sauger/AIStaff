import { useMemo } from 'react';
import {
  Badge,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { ComponentType, SVGProps } from 'react';
import { cn } from '@/lib/utils';
import { teamIdFromScope, toTeamScope } from '@/lib/agent-scope-storage';
import EmployeeAvatar from './EmployeeAvatar';
import BrandLogo from './BrandLogo';
import StaffdeckIcon from './StaffdeckIcon';
import { employeeDisplayNameWithCreator, employeeProfile, staffdeckDisplayText } from '../employee';
import { EnterpriseRoute } from '../enums/routes';
import type { AgentProfileRead, ChatSession, TeamRead } from '../types';
import IconPlatform from '../assets/icons/nav-platform.svg?react';
import IconAgents from '../assets/icons/nav-agents.svg?react';
import IconTeams from '../assets/icons/nav-teams.svg?react';
import IconFile from '../assets/icons/profile-file.svg?react';
import IconAlarm from '../assets/icons/profile-alarm.svg?react';
import IconHistory from '../assets/icons/profile-history.svg?react';
import IconCalendar from '../assets/icons/profile-calendar.svg?react';
import IconFolder from '../assets/icons/cap-folder.svg?react';
import IconCabinet from '../assets/icons/cap-cabinet.svg?react';
import IconMail from '../assets/icons/cap-mail.svg?react';
import IconSecret from '../assets/icons/cap-secret.svg?react';
import IconLoginGuide from '../assets/icons/cap-login-guide.svg?react';
import IconMagicWand from '../assets/icons/cap-magicwand.svg?react';
import IconClipboard from '../assets/icons/cap-clipboard.svg?react';
import IconBriefcase from '../assets/icons/cap-briefcase.svg?react';
import IconChat from '../assets/icons/action-chat.svg?react';
import IconToggle from '../assets/icons/action-toggle.svg?react';
import IconHeaderCollapse from '../assets/icons/header-collapse.svg?react';
import IconAccounts from '../assets/icons/sys-accounts.svg?react';
import IconModels from '../assets/icons/sys-models.svg?react';
import IconSettings from '../assets/icons/action-toggle.svg?react';
import IconChevronDown from '../assets/icons/chevron-down.svg?react';
import IconAdd from '../assets/icons/add.svg?react';
import IconSort from '../assets/icons/sort.svg?react';
import IconGlobe from '../assets/icons/globe.svg?react';
import IconViewMasonry from '../assets/icons/view-masonry.svg?react';
import IconChatBubble from '../assets/icons/chat.svg?react';
import IconEdit from '../assets/icons/edit.svg?react';
import IconTrash from '../assets/icons/trash.svg?react';
