import {useState} from 'react'
import {render,screen,fireEvent} from '@testing-library/react'
import {describe,it,expect,vi} from 'vitest'
import PixelQuestions from './PixelQuestions'
import {parseQuestionsFrame,questionMetadata,answersMessage,isQuestionAnswer} from '../lib/pixelQuestions'
import {continueGoal} from '../lib/portalGoal'
const questions=[{id:'style',question:'Qual estilo?',options:['Clean','Colorido']},{id:'pages',question:'Quantas páginas?',options:['Uma','Três']}]
function Fixture({onSubmit,...props}) {
  const [answers,setAnswers]=useState({})
  return <PixelQuestions questions={questions} answers={answers} onChange={setAnswers} onSubmit={onSubmit} {...props}/>
}
describe('choice cards',()=>{
  it('requires actual choices, supports custom text and submits both answers once',()=>{
    const submit=vi.fn();render(<Fixture onSubmit={submit}/>);
    expect(screen.getByRole('button',{name:'Next'})).toBeDisabled();
    fireEvent.click(screen.getByRole('radio',{name:/Clean/}));
    fireEvent.click(screen.getByRole('button',{name:'Next'}));
    fireEvent.click(screen.getByRole('radio',{name:'Write another answer'}));
    fireEvent.change(screen.getByRole('textbox',{name:'Your answer'}),{target:{value:'Duas páginas'}});
    fireEvent.click(screen.getByRole('button',{name:'Continue'}));
    expect(submit).toHaveBeenCalledWith('Qual estilo?\nClean\n\nQuantas páginas?\nDuas páginas');
  });
  it('retains answers when navigating back and disables old conversation questions',()=>{
    const {rerender}=render(<Fixture onSubmit={()=>{}}/>);
    fireEvent.click(screen.getByRole('radio',{name:/Colorido/}));fireEvent.click(screen.getByRole('button',{name:'Next'}));
    fireEvent.click(screen.getByRole('button',{name:'Previous question'}));
    expect(screen.getByRole('radio',{name:/Colorido/})).toBeChecked();
    rerender(<Fixture onSubmit={()=>{}} answered/>);
    expect(screen.queryByRole('button',{name:'Continue'})).not.toBeInTheDocument();
  });
  it('only accepts terminal structured frames; drafts persist as bounded text',()=>{
    expect(parseQuestionsFrame({choices:[{finish_reason:null}],pixel_questions:{schemaVersion:1,questions}})).toBeNull();
    expect(parseQuestionsFrame({choices:[{finish_reason:'stop'}],pixel_questions:{schemaVersion:1,questions}})).toEqual(questions);
    expect(questionMetadata({role:'assistant',questions,questionDraft:{style:'Clean'}}).questionDraft.style).toBe('Clean');
    expect(answersMessage(questions,{style:'Clean'})).toBeNull();
  });
  it('retains selected answers in the completed card after reload without editable controls',()=>{
    const saved=JSON.parse(JSON.stringify({role:'assistant',questions,questionDraft:{style:'Clean',pages:'Duas páginas'}}))
    const metadata=questionMetadata(saved)
    render(<PixelQuestions {...metadata} answers={metadata.questionDraft} answered/>);
    expect(screen.getByRole('region',{name:'Your answers'})).toHaveTextContent('Qual estilo?')
    expect(screen.getByText('Clean')).toBeVisible()
    expect(screen.getByText('Duas páginas')).toBeVisible()
    expect(screen.queryByRole('radio')).toBeNull()
    expect(screen.queryByRole('button',{name:'Continue'})).toBeNull()
  });
  it('hides only the exact answer turn represented in the preceding card, preserving ordinary prompts',()=>{
    const messages=[{role:'assistant',questions,questionDraft:{style:'Clean',pages:'Uma'}},
      {role:'user',content:'Qual estilo?\nClean\n\nQuantas páginas?\nUma'}]
    const before=JSON.stringify(messages)
    expect(isQuestionAnswer(messages,1)).toBe(true)
    expect(JSON.stringify(messages)).toBe(before)
    expect(isQuestionAnswer([messages[0],{role:'user',content:'Actually, a different task'}],1)).toBe(false)
    expect(isQuestionAnswer([{...messages[0],questionDraft:{}},messages[1]],1)).toBe(false)
    expect(isQuestionAnswer([messages[1]],0)).toBe(false)
  });
  it('recognizes a goal continuation answer without hiding a different follow-up',()=>{
    const messages=[{role:'user',content:'/goal Create a cafe site'},
      {role:'assistant',content:'Choose the style first.',questions,questionDraft:{style:'Clean',pages:'Uma'},
        task:{goal:{steps:[{label:'Create the page',status:'pending'}]}}}]
    const content=continueGoal(messages,1,answersMessage(questions,messages[1].questionDraft))
    expect(isQuestionAnswer([...messages,{role:'user',content}],2)).toBe(true)
    expect(isQuestionAnswer([...messages,{role:'user',content:'Cancel that goal.'}],2)).toBe(false)
  });
});
